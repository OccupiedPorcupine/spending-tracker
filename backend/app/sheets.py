"""Access to the `Daily Spending` tab.

The rest of the app talks to a `SheetBackend`, which has a few semantic operations. `GoogleSheet`
implements them with the Sheets API; `FakeSheet` (tests) implements them over an in-memory grid
that computes Q:W the way the real sheet's per-row formulas do.

Layout (row 1 is headers; data starts at row 2):
  A amount (signed)  B date  C category  D required  E currency  F notes  G entry ID  H source
  Q completed (formula)  R:T month/day/year (formulas)  U:W amount in SGD/MYR/USD (formulas)
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

COLS = 23  # A..W
COL = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVW")}
FORMULA_COLS = range(COL["Q"], COL["W"] + 1)
# The Dashboard's named ranges (DS_*) stop at row 1001; a row below that is invisible to it.
ROW_LIMIT = 1001
ROW_WARN = 950
DATE_FORMAT = "m/d/yyyy"
EPOCH = dt.date(1899, 12, 30)  # Sheets serial date 0


def to_serial(d: dt.date) -> int:
    return (d - EPOCH).days


def from_serial(v: Any) -> dt.date | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return EPOCH + dt.timedelta(days=int(v))


def is_blank(v: Any) -> bool:
    return v is None or v == ""


@dataclass
class SheetMeta:
    title: str
    gid: int
    locale: str
    time_zone: str


@dataclass
class RowCheck:
    a_is_number: bool
    b_is_date: bool
    b_formatted: str
    u_is_number: bool
    completed: Any

    @property
    def ok(self) -> bool:
        return self.a_is_number and self.b_is_date and self.u_is_number


class SheetBackend(Protocol):
    def meta(self) -> SheetMeta: ...
    def grid(self, formulas: bool) -> list[list[Any]]:
        """Rows 2.. as lists of A..W. formulas=True gives formulas for formula cells and raw values
        otherwise; formulas=False gives computed values. Dates are serial numbers either way."""
        ...
    def write_row(self, row: int, cells: list[Any], copy_formulas_from: int | None) -> None:
        """Write A..H of `row` (typed values, date in B as a serial with DATE_FORMAT), after
        optionally copying the Q:W formulas from another row."""
        ...
    def update_row(self, row: int, cells: list[Any]) -> None: ...
    def check_row(self, row: int) -> RowCheck: ...
    def delete_row(self, row: int) -> None: ...


def pad(r: list[Any]) -> list[Any]:
    return (list(r) + [None] * COLS)[:COLS]


# --------------------------------------------------------------------------- Google implementation

class GoogleSheet:
    """Blocking calls; the writer runs them in a worker thread."""

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(self, spreadsheet_id: str, tab: str, sa_file: str, timeout: float = 15):
        import google_auth_httplib2
        import httplib2
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(sa_file, scopes=self.SCOPES)
        self._local = threading.local()

        def service():
            # httplib2 is not thread-safe, so each thread gets its own client.
            if not hasattr(self._local, "svc"):
                http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout))
                self._local.svc = build("sheets", "v4", http=http, cache_discovery=False)
            return self._local.svc

        self._service = service
        self.id = spreadsheet_id
        self.tab = tab
        self._meta: SheetMeta | None = None

    def _a1(self, rng: str) -> str:
        return "'" + self.tab.replace("'", "''") + "'!" + rng

    def meta(self, refresh: bool = False) -> SheetMeta:
        if self._meta and not refresh:
            return self._meta
        r = self._service().spreadsheets().get(
            spreadsheetId=self.id, fields="properties(title,locale,timeZone),sheets.properties(sheetId,title)"
        ).execute()
        gid = next((s["properties"]["sheetId"] for s in r["sheets"] if s["properties"]["title"] == self.tab), None)
        if gid is None:
            raise RuntimeError(f"tab {self.tab!r} not found")
        p = r["properties"]
        self._meta = SheetMeta(p["title"], gid, p.get("locale", ""), p.get("timeZone", ""))
        return self._meta

    def grid(self, formulas: bool) -> list[list[Any]]:
        r = self._service().spreadsheets().values().get(
            spreadsheetId=self.id, range=self._a1("A2:W"), majorDimension="ROWS",
            valueRenderOption="FORMULA" if formulas else "UNFORMATTED_VALUE",
            dateTimeRenderOption="SERIAL_NUMBER",
        ).execute()
        return [pad(row) for row in r.get("values", [])]

    @staticmethod
    def _cell(v: Any) -> dict:
        if v is None or v == "":
            return {}
        if isinstance(v, bool):
            return {"userEnteredValue": {"boolValue": v}}
        if isinstance(v, (int, float)):
            return {"userEnteredValue": {"numberValue": v}}
        # Always a plain string: text that starts with "=" is stored as text, never run as a formula.
        return {"userEnteredValue": {"stringValue": str(v)}}

    def _row_requests(self, row: int, cells: list[Any]) -> list[dict]:
        gid = self.meta().gid
        return [
            {"updateCells": {
                "start": {"sheetId": gid, "rowIndex": row - 1, "columnIndex": 0},
                "rows": [{"values": [self._cell(v) for v in cells]}],
                "fields": "userEnteredValue",
            }},
            {"repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": row - 1, "endRowIndex": row,
                          "startColumnIndex": COL["B"], "endColumnIndex": COL["B"] + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": DATE_FORMAT}}},
                "fields": "userEnteredFormat.numberFormat",
            }},
        ]

    def _batch(self, requests: list[dict]) -> None:
        self._service().spreadsheets().batchUpdate(spreadsheetId=self.id, body={"requests": requests}).execute()

    def write_row(self, row: int, cells: list[Any], copy_formulas_from: int | None) -> None:
        reqs: list[dict] = []
        if copy_formulas_from:
            gid = self.meta().gid
            span = {"startColumnIndex": COL["Q"], "endColumnIndex": COL["W"] + 1}
            reqs.append({"copyPaste": {
                "source": {"sheetId": gid, "startRowIndex": copy_formulas_from - 1, "endRowIndex": copy_formulas_from, **span},
                "destination": {"sheetId": gid, "startRowIndex": row - 1, "endRowIndex": row, **span},
                "pasteType": "PASTE_FORMULA",
            }})
        self._batch(reqs + self._row_requests(row, cells))

    def update_row(self, row: int, cells: list[Any]) -> None:
        self._batch(self._row_requests(row, cells))

    def check_row(self, row: int) -> RowCheck:
        r = self._service().spreadsheets().get(
            spreadsheetId=self.id, ranges=[self._a1(f"A{row}:W{row}")],
            fields="sheets.data.rowData.values(effectiveValue,formattedValue,effectiveFormat.numberFormat)",
        ).execute()
        vals = (r["sheets"][0]["data"][0].get("rowData") or [{}])[0].get("values", [])
        vals = (vals + [{}] * COLS)[:COLS]
        num = lambda c: "numberValue" in (vals[COL[c]].get("effectiveValue") or {})  # noqa: E731
        b_fmt = ((vals[COL["B"]].get("effectiveFormat") or {}).get("numberFormat") or {}).get("type", "")
        q = (vals[COL["Q"]].get("effectiveValue") or {}).get("boolValue")
        return RowCheck(num("A"), num("B") and b_fmt in ("DATE", "DATE_TIME"),
                        vals[COL["B"]].get("formattedValue", ""), num("U"), q)

    def delete_row(self, row: int) -> None:
        self._batch([{"deleteDimension": {"range": {
            "sheetId": self.meta().gid, "dimension": "ROWS", "startIndex": row - 1, "endIndex": row}}}])


# --------------------------------------------------------------------------- fake for tests

class FakeSheet:
    """In-memory stand-in. Formula columns hold the same per-row formulas as the real sheet
    (as strings) and are evaluated on read, with fixed exchange rates."""

    MYR_TO_SGD = 0.3115
    SGD_TO_USD = 0.7738

    def __init__(self, rows: list[list[Any]] | None = None, formula_rows: int = ROW_LIMIT, title: str = "2026 Journal (dev)"):
        self.title = title
        self.rows: dict[int, list[Any]] = {}
        for r in range(2, formula_rows + 1):
            self.rows[r] = [None] * COLS
            for c in FORMULA_COLS:
                self.rows[r][c] = self.formula(c, r)
        for i, data in enumerate(rows or []):
            for c, v in enumerate(data):
                if v is not None:
                    self.rows[i + 2][c] = v
        self.calls: list[str] = []
        self.fail_next: Exception | None = None

    @classmethod
    def demo(cls) -> "FakeSheet":
        """A few weeks of made-up rows for local development."""
        import random
        rnd = random.Random(7)
        t = dt.date.today()
        picks = [("Food", "Yes", "SGD", "lunch", 4.5), ("Food", "Yes", "SGD", "Kopi", 1.4), ("Food", "No", "SGD", "Mixue", 3.2),
                 ("Things", "Yes", "SGD", "Grab", 12.0), ("Food", "Yes", "MYR", "dinner", 18.0), ("Things", "No", "SGD", "Headphones", 89.0)]
        rows = []
        for i in range(40, 0, -1):
            c, r, cur, n, a = rnd.choice(picks)
            rows.append([-round(a * rnd.uniform(0.8, 1.3), 2), to_serial(t - dt.timedelta(days=i // 2)), c, r, cur, n])
        rows.insert(10, [350.0, to_serial(t - dt.timedelta(days=20)), "Revenue", "Yes", "SGD", "Eason"])
        rows += [[-7.8], [-1.6]]
        return cls([r + [None] * (COLS - len(r)) for r in rows], formula_rows=200)

    @staticmethod
    def formula(col: int, r: int) -> str:
        return {
            COL["Q"]: f"=NOT(OR(ISBLANK(E{r}), ISBLANK(D{r}), ISBLANK(C{r}), ISBLANK(B{r}), ISBLANK(A{r})))",
            COL["R"]: f"=MONTH($B{r})", COL["S"]: f"=DAY($B{r})", COL["T"]: f"=YEAR($B{r})",
            COL["U"]: f'=IF($E{r}="SGD", $A{r},$A{r}*MYRtoSGD)',
            COL["V"]: f'=IF($E{r}="MYR", $A{r},$A{r}/MYRtoSGD)',
            COL["W"]: f"=$U{r}*SGDtoUSD",
        }[col]

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e

    def _eval(self, r: int) -> list[Any]:
        row = list(self.rows.get(r, [None] * COLS))
        a, b, e = row[0], row[1], row[4]
        has = {c: isinstance(row[c], str) and row[c].startswith("=") for c in FORMULA_COLS}
        num = isinstance(a, (int, float)) and not isinstance(a, bool)
        d = from_serial(b)
        if has[COL["Q"]]:
            row[COL["Q"]] = all(not is_blank(row[c]) for c in range(5))
        for c, f in ((COL["R"], lambda x: x.month), (COL["S"], lambda x: x.day), (COL["T"], lambda x: x.year)):
            if has[c]:
                row[c] = f(d) if d else (12 if c == COL["R"] else 30 if c == COL["S"] else 1899)
        u = (a if e == "SGD" else a * self.MYR_TO_SGD) if num else 0
        if has[COL["U"]]:
            row[COL["U"]] = u
        if has[COL["V"]]:
            row[COL["V"]] = (a if e == "MYR" else a / self.MYR_TO_SGD) if num else 0
        if has[COL["W"]]:
            row[COL["W"]] = u * self.SGD_TO_USD
        return row

    def meta(self) -> SheetMeta:
        self._maybe_fail("meta")
        return SheetMeta(self.title, 0, "en_US", "Asia/Singapore")

    def _last(self) -> int:
        return max(self.rows, default=1)

    def grid(self, formulas: bool) -> list[list[Any]]:
        self._maybe_fail("grid")
        out = [list(self.rows.get(r, [None] * COLS)) if formulas else self._eval(r) for r in range(2, self._last() + 1)]
        while out and all(is_blank(v) for v in out[-1]):
            out.pop()
        return out

    def write_row(self, row: int, cells: list[Any], copy_formulas_from: int | None) -> None:
        self._maybe_fail("write_row")
        self.rows.setdefault(row, [None] * COLS)
        if copy_formulas_from:
            for c in FORMULA_COLS:
                self.rows[row][c] = self.formula(c, row)
        self.update_row(row, cells, _record=False)

    def update_row(self, row: int, cells: list[Any], _record: bool = True) -> None:
        if _record:
            self._maybe_fail("update_row")
        for c, v in enumerate(cells):
            self.rows[row][c] = None if v == "" else v

    def check_row(self, row: int) -> RowCheck:
        self._maybe_fail("check_row")
        v = self._eval(row)
        num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)  # noqa: E731
        d = from_serial(v[1])
        return RowCheck(num(v[0]), d is not None, f"{d.month}/{d.day}/{d.year}" if d else "", num(v[COL["U"]]), v[COL["Q"]])

    def delete_row(self, row: int) -> None:
        self._maybe_fail("delete_row")
        last = self._last()
        for r in range(row, last):
            nxt = list(self.rows.get(r + 1, [None] * COLS))
            # formulas are relative, so a shifted row's formulas now refer to its new row number
            for c in FORMULA_COLS:
                if isinstance(nxt[c], str) and nxt[c].startswith("="):
                    nxt[c] = self.formula(c, r)
            self.rows[r] = nxt
        self.rows.pop(last, None)

    # test helpers
    def sort_rows(self, key) -> None:
        """Sort whole data rows like a manual sort over A:W would."""
        rows = [self.rows[r] for r in sorted(self.rows) if not is_blank(self.rows[r][0])]
        rows.sort(key=key)
        for i, data in enumerate(rows):
            r = i + 2
            fixed = list(data)
            for c in FORMULA_COLS:
                fixed[c] = self.formula(c, r)
            self.rows[r] = fixed

    def insert_blank_row(self, before: int) -> None:
        """Insert an empty row without formulas, like Insert row above."""
        last = self._last()
        for r in range(last, before - 1, -1):
            src = list(self.rows[r])
            for c in FORMULA_COLS:
                if isinstance(src[c], str) and src[c].startswith("="):
                    src[c] = self.formula(c, r + 1)
            self.rows[r + 1] = src
        self.rows[before] = [None] * COLS


class Timer:
    def __init__(self) -> None:
        self.t = time.monotonic()

    def ms(self) -> int:
        return int((time.monotonic() - self.t) * 1000)
