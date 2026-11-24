"""The single path to the sheet. Every write and delete goes through one lock, re-reads the sheet
right before acting, and finds rows by the entry ID in column G, never by a remembered row number.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .sheets import (COL, FORMULA_COLS, ROW_LIMIT, SheetBackend, Timer, from_serial, is_blank, to_serial)

log = logging.getLogger("spend.sheet")

DEFAULT_CATEGORIES = ["Food", "Things", "Revenue", "Loan"]
CATEGORY_TTL = 600  # seconds
VALUES_TTL = 20


class SheetError(Exception):
    """A write that could not be completed safely. `retry` says whether trying again may help."""

    def __init__(self, msg: str, retry: bool = True):
        super().__init__(msg)
        self.retry = retry


@dataclass
class WriteResult:
    row: int
    verified: bool
    already: bool = False
    detail: str = ""


@dataclass
class Health:
    ok: bool | None = None
    at: int | None = None
    error: str | None = None


@dataclass
class SheetService:
    backend_factory: Callable[[], SheetBackend]
    _backend: SheetBackend | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    health: Health = field(default_factory=Health)
    _values: tuple[float, list[list[Any]]] | None = None
    _cats: tuple[float, list[str]] | None = None

    @property
    def backend(self) -> SheetBackend:
        if self._backend is None:
            self._backend = self.backend_factory()
        return self._backend

    async def call(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run one blocking sheet call in a thread and record whether it worked (for /api/health)."""
        try:
            out = await asyncio.to_thread(fn, *args)
        except Exception as e:  # noqa: BLE001
            self.health = Health(False, int(time.time() * 1000), type(e).__name__)
            raise
        self.health = Health(True, int(time.time() * 1000), None)
        return out

    # ------------------------------------------------------------------ reads

    def invalidate(self) -> None:
        self._values = None

    def reset(self) -> None:
        """Forget the backend and every cache: the next call opens the (newly) active sheet."""
        self._backend = None
        self._values = None
        self._cats = None
        self.health = Health()

    async def values(self, max_age: float = VALUES_TTL) -> list[list[Any]]:
        """Computed values of A2:W, cached briefly."""
        if self._values and time.monotonic() - self._values[0] < max_age:
            return self._values[1]
        rows = await self.call(lambda: self.backend.grid(False))
        self._values = (time.monotonic(), rows)
        return rows

    async def categories(self) -> list[str]:
        if self._cats and time.monotonic() - self._cats[0] < CATEGORY_TTL:
            return self._cats[1]
        try:
            rows = await self.values()
            seen = Counter(r[COL["C"]] for r in rows if isinstance(r[COL["C"]], str) and r[COL["C"]].strip())
            cats = [c for c, _ in seen.most_common()]
        except Exception:  # noqa: BLE001
            cats = []
        # The sheet's validation list also allows the defaults, so keep them even if unused so far.
        cats += [c for c in DEFAULT_CATEGORIES if c not in cats]
        self._cats = (time.monotonic(), cats)
        return cats

    async def meta(self):
        return await self.call(self.backend.meta)

    # ------------------------------------------------------------------ writes

    @staticmethod
    def find_id(grid: list[list[Any]], entry_id: str) -> int | None:
        for i, r in enumerate(grid):
            if r[COL["G"]] == entry_id:
                return i + 2
        return None

    @staticmethod
    def last_used_row(grid: list[list[Any]]) -> int:
        """Row number of the last non-empty cell in column A (1 if there is no data)."""
        last = 1
        for i, r in enumerate(grid):
            if not is_blank(r[COL["A"]]):
                last = i + 2
        return last

    @staticmethod
    def has_formulas(row: list[Any]) -> bool:
        return all(isinstance(row[c], str) and row[c].startswith("=") for c in FORMULA_COLS)

    async def write(self, entry_id: str, cells: list[Any]) -> WriteResult:
        """Write one entry (A..H) unless column G already has its ID."""
        async with self.lock:
            t = Timer()
            grid = await self.call(lambda: self.backend.grid(True))
            existing = self.find_id(grid, entry_id)
            if existing:
                log.info("entry %s already in sheet (row %s)", entry_id, existing)
                return WriteResult(existing, True, already=True)
            target = self.last_used_row(grid) + 1
            if target > ROW_LIMIT:
                raise SheetError(f"sheet is full: row {target} is past {ROW_LIMIT}, where the Dashboard's ranges end", retry=False)
            row = grid[target - 2] if target - 2 < len(grid) else [None] * 23
            busy = [c for c in "ABCDEFGHIJKLMNOP" if not is_blank(row[COL[c]])]
            if busy:
                raise SheetError(f"target row {target} is not empty (columns {''.join(busy)})")
            copy_from = None
            if not self.has_formulas(row):
                copy_from = next((r + 2 for r in range(target - 3, -1, -1) if self.has_formulas(grid[r])), None)
                if copy_from is None:
                    raise SheetError("no row with the Q:W formulas to copy from", retry=False)
            await self.call(self.backend.write_row, target, cells, copy_from)
            self.invalidate()
            check = await self.call(self.backend.check_row, target)
            detail = "" if check.ok else f"read-back: A number {check.a_is_number}, B date {check.b_is_date}, U number {check.u_is_number}"
            log.info("entry %s written to row %s in %d ms%s%s", entry_id, target, t.ms(),
                     f" (formulas copied from row {copy_from})" if copy_from else "", f" WARNING {detail}" if detail else "")
            return WriteResult(target, check.ok, detail=detail)

    async def _locate(self, entry_id: str, fingerprint: str | None) -> tuple[int | None, list[list[Any]]]:
        grid = await self.call(lambda: self.backend.grid(False))
        row = self.find_id(grid, entry_id)
        if row and fingerprint is not None and fingerprint_of(grid[row - 2]) != fingerprint:
            raise SheetError(f"row {row} has entry {entry_id} but its amount, date or notes changed in the sheet; "
                             "not touching it", retry=False)
        return row, grid

    async def delete(self, entry_id: str, fingerprint: str | None) -> int | None:
        """Delete the row holding `entry_id`. Returns the row number, or None if it is already gone."""
        async with self.lock:
            row, _ = await self._locate(entry_id, fingerprint)
            if row is None:
                return None
            await self.call(self.backend.delete_row, row)
            self.invalidate()
            log.info("entry %s deleted (was row %s)", entry_id, row)
            return row

    async def update(self, entry_id: str, fingerprint: str | None, cells: list[Any]) -> int:
        async with self.lock:
            row, _ = await self._locate(entry_id, fingerprint)
            if row is None:
                raise SheetError(f"entry {entry_id} is not in the sheet", retry=False)
            await self.call(self.backend.update_row, row, cells)
            self.invalidate()
            log.info("entry %s updated (row %s)", entry_id, row)
            return row


def fingerprint_of(row: list[Any]) -> str:
    a, b, f = row[COL["A"]], row[COL["B"]], row[COL["F"]]
    amount = f"{a:.2f}" if isinstance(a, (int, float)) and not isinstance(a, bool) else str(a)
    serial = str(int(b)) if isinstance(b, (int, float)) and not isinstance(b, bool) else str(b)
    return f"{amount}|{serial}|{f or ''}"


# ---------------------------------------------------------------------- read models

TZ = ZoneInfo("Asia/Singapore")


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def row_view(i: int, r: list[Any]) -> dict:
    d = from_serial(r[COL["B"]])
    a = r[COL["A"]]
    return {
        "row": i + 2,
        "amount": a if isinstance(a, (int, float)) and not isinstance(a, bool) else None,
        "date": d.isoformat() if d else None,
        "category": r[COL["C"]] or None,
        "required": r[COL["D"]] or None,
        "currency": r[COL["E"]] or None,
        "notes": r[COL["F"]] or "",
        "id": r[COL["G"]] or None,
        "source": r[COL["H"]] or None,
        "sgd": r[COL["U"]] if isinstance(r[COL["U"]], (int, float)) else None,
    }


def recent(grid: list[list[Any]], n: int = 15) -> list[dict]:
    """The last n rows with an amount, bottom of the sheet first."""
    out = []
    for i in range(len(grid) - 1, -1, -1):
        if not is_blank(grid[i][COL["A"]]):
            out.append(row_view(i, grid[i]))
            if len(out) == n:
                break
    return out


def totals(grid: list[list[Any]], on: dt.date | None = None) -> dict:
    """Spending (everything that is not Revenue) in SGD from column U, as positive numbers."""
    on = on or today()
    week_start = on - dt.timedelta(days=on.weekday())
    t = {"today": 0.0, "week": 0.0, "month": 0.0, "income_month": 0.0}
    for r in grid:
        d, u, cat = from_serial(r[COL["B"]]), r[COL["U"]], r[COL["C"]]
        if not d or not isinstance(u, (int, float)) or isinstance(u, bool) or not r[COL["Q"]]:
            continue
        if cat == "Revenue":
            if (d.year, d.month) == (on.year, on.month):
                t["income_month"] += u
            continue
        spent = -u
        if d == on:
            t["today"] += spent
        if week_start <= d <= on:
            t["week"] += spent
        if (d.year, d.month) == (on.year, on.month) and d <= on:
            t["month"] += spent
    return {k: round(v, 2) for k, v in t.items()}


def note_suggestions(grid: list[list[Any]], limit: int = 300) -> list[dict]:
    """Distinct notes, most used first, each with the category, required flag and currency it had last time."""
    count: Counter[str] = Counter()
    last: dict[str, dict] = {}
    best: dict[str, tuple] = {}
    for r in grid:
        n = r[COL["F"]]
        if not isinstance(n, str) or not n.strip() or not r[COL["C"]]:
            continue
        key = n.strip()
        count[key] += 1
        when = (r[COL["B"]] if isinstance(r[COL["B"]], (int, float)) else 0,)
        if key not in best or when >= best[key]:
            best[key] = when
            last[key] = {"notes": key, "category": r[COL["C"]], "required": r[COL["D"]] == "Yes",
                         "currency": r[COL["E"]] if r[COL["E"]] in ("SGD", "MYR") else None}
    return [last[k] | {"uses": c} for k, c in count.most_common(limit)]


def recent_examples(grid: list[list[Any]], limit: int = 20) -> list[dict]:
    """The most recently dated distinct notes, each with its category, required flag and currency.
    This is all the AI parser ever sees of the sheet: no amounts, dates, totals or row numbers."""
    rows = []
    for i, r in enumerate(grid):
        n, cat, cur = r[COL["F"]], r[COL["C"]], r[COL["E"]]
        if not isinstance(n, str) or not n.strip() or not cat or cur not in ("SGD", "MYR"):
            continue
        b = r[COL["B"]]
        rows.append(((b if isinstance(b, (int, float)) and not isinstance(b, bool) else 0), i, n.strip(), cat,
                     r[COL["D"]] != "No", cur))
    out, seen = [], set()
    for _, _, n, cat, req, cur in sorted(rows, reverse=True):
        if n.lower() in seen:
            continue
        seen.add(n.lower())
        out.append({"notes": n[:60], "category": cat, "required": req, "currency": cur})
        if len(out) == limit:
            break
    return out


def cells_for(entry: dict) -> list[Any]:
    """A..H for an entry payload."""
    amount = round(float(entry["amount"]), 2)
    signed = -amount if entry["direction"] == "expense" else amount
    return [
        signed,
        to_serial(dt.date.fromisoformat(entry["date"])),
        entry["category"],
        "Yes" if entry["required"] else "No",
        entry["currency"],
        entry.get("notes") or "",
        entry["id"],
        entry.get("source") or "app",
    ]


def fingerprint_for(entry: dict) -> str:
    c = cells_for(entry)
    return fingerprint_of(c + [None] * 15)
