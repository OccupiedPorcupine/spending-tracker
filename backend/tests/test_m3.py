"""M3: editing rows the app wrote, favourites, and the sheet title used for the year warning."""
import datetime as dt
import json

from app.db import connect, now_ms
from app.sheets import COL, from_serial

from conftest import ORIGIN, entry


def fields(**kw):
    base = {"amount": 4.5, "direction": "expense", "category": "Food", "required": True,
            "currency": "SGD", "notes": "Chicken rice", "date": "2026-11-24"}
    return base | kw


def write(authed, **kw):
    e = entry(**kw)
    r = authed.post("/api/entries", json=e).json()
    assert r["status"] == "written"
    return e, r["row"]


# ------------------------------------------------------------------ edit

def test_edit_rewrites_a_to_f_and_keeps_id_and_source(authed):
    e, row = write(authed, source="app-ai")
    r = authed.post(f"/api/entries/{e['id']}/edit",
                    json=fields(amount=6.2, category="Things", required=False, currency="MYR", notes="Mixue", date="2026-11-23"))
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "written", "row": row, "verified": True}
    v = authed.sheet._eval(row)
    assert v[:8] == [-6.2, v[1], "Things", "No", "MYR", "Mixue", e["id"], "app-ai"]
    assert from_serial(v[1]) == dt.date(2026, 11, 23)
    assert v[COL["Q"]] is True and v[COL["U"]] < 0  # still computing from the edited A and E
    conn = connect()
    led = conn.execute("SELECT payload, fingerprint FROM entries WHERE id = ?", (e["id"],)).fetchone()
    hist = conn.execute("SELECT before, after FROM entry_edits WHERE entry_id = ?", (e["id"],)).fetchall()
    conn.close()
    assert json.loads(led["payload"])["notes"] == "Mixue" and led["fingerprint"].startswith("-6.20|")
    assert len(hist) == 1 and json.loads(hist[0]["before"])["notes"] == "Chicken rice"


def test_edit_can_switch_to_income(authed):
    e, row = write(authed)
    authed.post(f"/api/entries/{e['id']}/edit", json=fields(amount=50, direction="income", category="Revenue", notes="Fees"))
    assert authed.sheet._eval(row)[0] == 50


def test_edit_finds_the_row_after_a_manual_sort(authed):
    e1, _ = write(authed, notes="Keep", amount=1.0)
    e2, _ = write(authed, notes="Change me", amount=9.99)
    authed.sheet.sort_rows(key=lambda v: v[0])
    assert authed.post(f"/api/entries/{e2['id']}/edit", json=fields(amount=8, notes="Changed")).status_code == 200
    rows = {v[COL["G"]]: v for v in authed.sheet.rows.values() if v[0] is not None}
    assert rows[e2["id"]][0] == -8 and rows[e2["id"]][COL["F"]] == "Changed"
    assert rows[e1["id"]][0] == -1.0 and rows[e1["id"]][COL["F"]] == "Keep"


def test_edit_twice_and_retrying_an_edit_are_fine(authed):
    e, row = write(authed)
    for _ in range(2):  # the same edit again (e.g. a retry after a timeout) is not mistaken for a hand change
        assert authed.post(f"/api/entries/{e['id']}/edit", json=fields(amount=5)).status_code == 200
    assert authed.post(f"/api/entries/{e['id']}/edit", json=fields(amount=6)).status_code == 200
    assert authed.sheet._eval(row)[0] == -6
    # and delete still works with the updated fingerprint
    assert authed.post(f"/api/entries/{e['id']}/delete", json={}).json()["status"] == "deleted"


def test_edit_refuses_a_row_changed_by_hand(authed):
    e, row = write(authed)
    authed.sheet.rows[row][COL["F"]] = "fixed by hand"
    r = authed.post(f"/api/entries/{e['id']}/edit", json=fields(amount=7))
    assert r.status_code == 409 and "changed in the sheet" in r.json()["detail"]
    assert authed.sheet.rows[row][0] == -4.5 and authed.sheet.rows[row][COL["F"]] == "fixed by hand"


def test_edit_refuses_rows_the_app_did_not_make(authed):
    r = authed.post("/api/entries/00000000-0000-4000-8000-000000000000/edit", json=fields())
    assert r.status_code == 404


def test_edit_refuses_entries_not_yet_written_or_deleted(authed):
    conn = connect()
    conn.execute("INSERT INTO entries (id, payload, status, sheet_id, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?)",
                 ("00000000-0000-4000-8000-000000000001", json.dumps(entry()), "dev-sheet", now_ms(), now_ms()))
    conn.commit()
    conn.close()
    assert authed.post("/api/entries/00000000-0000-4000-8000-000000000001/edit", json=fields()).status_code == 409
    e, _ = write(authed)
    authed.post(f"/api/entries/{e['id']}/delete", json={})
    assert authed.post(f"/api/entries/{e['id']}/edit", json=fields()).status_code == 409


def test_edit_refuses_when_the_row_is_gone(authed):
    e, row = write(authed)
    authed.sheet.delete_row(row)
    r = authed.post(f"/api/entries/{e['id']}/edit", json=fields())
    assert r.status_code == 409 and "not in the sheet" in r.json()["detail"]


def test_edit_validates(authed):
    e, _ = write(authed)
    assert authed.post(f"/api/entries/{e['id']}/edit", json=fields(category="Nope")).status_code == 422
    assert authed.post(f"/api/entries/{e['id']}/edit", json=fields(amount=-1)).status_code == 422
    assert authed.post(f"/api/entries/{e['id']}/edit", json=fields(amount=1.234)).status_code == 422


def test_edit_sheet_down_is_503_and_changes_nothing(authed):
    e, row = write(authed)
    authed.sheet.fail_next = TimeoutError("slow")
    assert authed.post(f"/api/entries/{e['id']}/edit", json=fields(amount=9)).status_code == 503
    assert authed.sheet.rows[row][0] == -4.5


def test_edit_needs_exact_origin(authed):
    e, _ = write(authed)
    assert authed.post(f"/api/entries/{e['id']}/edit", json=fields(), headers={"Origin": "https://evil.example"}).status_code == 403


# ------------------------------------------------------------------ favourites

KOPI = {"name": "Kopi", "amount": 1.4, "category": "Food", "currency": "SGD", "notes": "Kopi"}


def test_favourites_add_list_delete(authed):
    r = authed.post("/api/favourites", json=KOPI)
    assert r.status_code == 200, r.text
    favs = r.json()["favourites"]
    assert favs == [{"id": favs[0]["id"], "name": "Kopi", "amount": 1.4, "direction": "expense", "category": "Food",
                     "required": True, "currency": "SGD", "notes": "Kopi"}]
    grab = {"name": "Grab", "category": "Things", "currency": "SGD", "notes": "Grab", "required": False}
    favs = authed.post("/api/favourites", json=grab).json()["favourites"]
    assert [f["name"] for f in favs] == ["Kopi", "Grab"] and favs[1]["amount"] is None
    assert authed.get("/api/meta").json()["favourites"] == favs
    favs = authed.post(f"/api/favourites/{favs[0]['id']}/delete", json={}).json()["favourites"]
    assert [f["name"] for f in favs] == ["Grab"]


def test_same_favourite_twice_is_kept_once(authed):
    authed.post("/api/favourites", json=KOPI)
    assert len(authed.post("/api/favourites", json=KOPI).json()["favourites"]) == 1


def test_favourite_validation(authed):
    assert authed.post("/api/favourites", json=KOPI | {"category": "Nope"}).status_code == 422
    assert authed.post("/api/favourites", json=KOPI | {"name": ""}).status_code == 422
    assert authed.post("/api/favourites", json=KOPI | {"amount": 0}).status_code == 422
    assert authed.post("/api/favourites", json=KOPI | {"direction": "income"}).status_code == 422
    assert authed.post("/api/favourites", json=KOPI | {"currency": "USD"}).status_code == 422


def test_favourites_are_capped(authed):
    for i in range(20):
        assert authed.post("/api/favourites", json=KOPI | {"name": f"K{i}"}).status_code == 200
    assert authed.post("/api/favourites", json=KOPI | {"name": "one more"}).status_code == 409


def test_favourites_need_session_and_origin(client):
    assert client.get("/api/favourites").status_code == 401
    assert client.post("/api/favourites", json=KOPI).status_code == 401
    assert client.post("/api/favourites/1/delete", json={}).status_code == 401


def test_favourite_origin_checked(authed):
    r = authed.post("/api/favourites", json=KOPI, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert authed.get("/api/favourites").json()["favourites"] == []


# ------------------------------------------------------------------ year warning

def test_meta_has_the_sheet_title(authed):
    assert authed.get("/api/meta").json()["sheet_title"] == "2026 Journal (dev)"
