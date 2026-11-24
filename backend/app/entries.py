"""Entries: create (idempotent on the client's ID), undo/delete, recent rows, totals and form metadata."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .config import settings
from .db import connect, get_db, get_setting, now_ms, set_setting
from .writer import (SheetError, SheetService, cells_for, fingerprint_for, note_suggestions, recent, today, totals)
from .sheets import ROW_LIMIT, ROW_WARN

log = logging.getLogger("spend.entries")
router = APIRouter(prefix="/api")
WRITE_WAIT = 12.0  # seconds a request waits for the sheet before answering "queued"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def service(request: Request) -> SheetService:
    return request.app.state.sheet


class Entry(BaseModel):
    id: str
    amount: float = Field(gt=0, le=1_000_000)
    direction: Literal["expense", "income"]
    category: str = Field(min_length=1, max_length=40)
    required: bool = True
    currency: Literal["SGD", "MYR"]
    notes: str = Field(default="", max_length=120)
    date: dt.date
    source: Literal["app", "app-ai"] = "app"

    @field_validator("id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        v = v.lower()
        if not UUID.match(v):
            raise ValueError("id must be a UUID")
        return v

    @field_validator("amount")
    @classmethod
    def _cents(cls, v: float) -> float:
        if abs(round(v, 2) - v) > 1e-9:
            raise ValueError("at most 2 decimal places")
        return round(v, 2)

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str) -> str:
        return " ".join(v.split())

    def payload(self) -> dict:
        d = self.model_dump()
        d["date"] = self.date.isoformat()
        return d


def _ledger(conn: sqlite3.Connection, entry_id: str):
    return conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()


async def write_from_ledger(svc: SheetService, entry_id: str) -> dict:
    """Try to write a ledger entry to the sheet and record the outcome. Safe to call any number of times."""
    conn = connect()
    try:
        row = _ledger(conn, entry_id)
        if row is None:
            raise KeyError(entry_id)
        if row["status"] in ("written", "deleted"):
            return {"status": row["status"], "row": row["sheet_row"]}
        payload = json.loads(row["payload"])
        conn.execute("UPDATE entries SET attempts = attempts + 1, updated_at = ? WHERE id = ?", (now_ms(), entry_id))
        conn.commit()
    finally:
        conn.close()
    try:
        res = await svc.write(entry_id, cells_for(payload))
    except Exception as e:  # noqa: BLE001
        retry = getattr(e, "retry", True)
        msg = str(e) if isinstance(e, SheetError) else type(e).__name__
        conn = connect()
        try:
            conn.execute("UPDATE entries SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
                         (msg[:300], now_ms(), entry_id))
            conn.commit()
        finally:
            conn.close()
        log.warning("entry %s failed: %s", entry_id, msg)
        return {"status": "failed", "error": msg, "retry": retry}
    conn = connect()
    try:
        now = now_ms()
        conn.execute(
            "UPDATE entries SET status = 'written', sheet_row = ?, fingerprint = ?, last_error = ?, "
            "written_at = ?, updated_at = ? WHERE id = ? AND status != 'deleted'",
            (res.row, fingerprint_for(payload), res.detail or None, now, now, entry_id))
        set_setting(conn, "last_currency", payload["currency"])
        set_setting(conn, "last_write_at", str(now))
        conn.commit()
    finally:
        conn.close()
    return {"status": "written", "row": res.row, "verified": res.verified,
            **({"warning": "row is near the end of the Dashboard's range"} if res.row >= ROW_WARN else {})}


@router.post("/entries")
async def create_entry(entry: Entry, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    svc = service(request)
    cats = await svc.categories()
    if entry.category not in cats:
        raise HTTPException(422, f"unknown category {entry.category!r}")
    row = _ledger(conn, entry.id)
    if row is None:
        now = now_ms()
        conn.execute(
            "INSERT INTO entries (id, payload, status, sheet_id, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?)",
            (entry.id, json.dumps(entry.payload()), settings().active_sheet_id, now, now))
        conn.commit()
    elif row["status"] == "written":
        return {"status": "written", "row": row["sheet_row"], "already": True}
    elif row["status"] == "deleted":
        return {"status": "deleted"}
    # Shield the write so it finishes even if this request gives up waiting.
    task = asyncio.ensure_future(write_from_ledger(svc, entry.id))
    try:
        res = await asyncio.wait_for(asyncio.shield(task), WRITE_WAIT)
    except asyncio.TimeoutError:
        return JSONResponse({"status": "queued", "error": "sheet is slow; the server will keep trying"}, status_code=202)
    if res["status"] == "failed":
        # 202: the server holds it in the ledger and retries; the client may also resend safely.
        return JSONResponse({"status": "queued", "error": res["error"], "retry": res["retry"]}, status_code=202)
    return res


class Empty(BaseModel):
    pass


@router.post("/entries/{entry_id}/delete")
async def delete_entry(entry_id: str, _: Empty, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    row = _ledger(conn, entry_id.lower())
    if row is None:
        raise HTTPException(404, "only entries made by this app can be deleted")
    if row["status"] == "deleted":
        return {"status": "deleted"}
    svc = service(request)
    if row["status"] != "written":
        # never reached the sheet; make sure it never will, then check the sheet anyway
        conn.execute("UPDATE entries SET status = 'deleted', updated_at = ? WHERE id = ?", (now_ms(), row["id"]))
        conn.commit()
    try:
        gone = await svc.delete(row["id"], row["fingerprint"])
    except SheetError as e:
        raise HTTPException(409 if not e.retry else 503, str(e)) from None
    conn.execute("UPDATE entries SET status = 'deleted', updated_at = ? WHERE id = ?", (now_ms(), row["id"]))
    return {"status": "deleted", "row": gone}


@router.get("/recent")
async def get_recent(request: Request, fresh: int = 0, conn: sqlite3.Connection = Depends(get_db)):
    svc = service(request)
    grid = await svc.values(0 if fresh else 20)
    rows = recent(grid, 15)
    ids = [r["id"] for r in rows if r["id"]]
    mine = {r["id"] for r in conn.execute(
        f"SELECT id FROM entries WHERE status = 'written' AND id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
    for r in rows:
        r["editable"] = r["id"] in mine
    used = svc.last_used_row(grid)
    return {"rows": rows, "totals": totals(grid), "today": today().isoformat(),
            "rows_left": ROW_LIMIT - used}


@router.get("/meta")
async def meta(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    svc = service(request)
    cats = await svc.categories()
    try:
        grid = await svc.values(600)
        notes = note_suggestions(grid)
    except Exception:  # noqa: BLE001
        notes = []
    s = settings()
    return {
        "categories": cats,
        "notes": notes,
        "last_currency": get_setting(conn, "last_currency", "SGD"),
        "target": s.sheet_target,
        "version": __version__,
        "today": today().isoformat(),
    }


@router.get("/status")
async def status(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    svc = service(request)
    try:
        m = await svc.meta()
        title, locale, tz = m.title, m.locale, m.time_zone
    except Exception as e:  # noqa: BLE001
        title, locale, tz = None, None, type(e).__name__
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM entries GROUP BY status")}
    last = get_setting(conn, "last_write_at")
    return {
        "sheet": {"title": title, "target": settings().sheet_target, "locale": locale, "time_zone": tz},
        "ledger": counts,
        "last_write_at": int(last) if last else None,
        "last_sheet_call": {"ok": svc.health.ok, "at": svc.health.at},
        "version": __version__,
    }


class SettingsBody(BaseModel):
    require_unlock: bool | None = None


@router.get("/settings")
def get_settings(conn: sqlite3.Connection = Depends(get_db)):
    return {"require_unlock": get_setting(conn, "require_unlock", "0") == "1"}


@router.post("/settings")
def put_settings(body: SettingsBody, conn: sqlite3.Connection = Depends(get_db)):
    if body.require_unlock is not None:
        set_setting(conn, "require_unlock", "1" if body.require_unlock else "0")
    return get_settings(conn)


async def retry_loop(svc: SheetService, every: float = 60) -> None:
    """Background: resend entries the ledger still holds as pending or failed."""
    while True:
        await asyncio.sleep(every)
        await retry_pending(svc)


async def retry_pending(svc: SheetService, min_age_ms: int = 30_000) -> int:
    conn = connect()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM entries WHERE status IN ('pending', 'failed') AND attempts < 50 AND updated_at < ? "
            "ORDER BY created_at", (now_ms() - min_age_ms,))]
    finally:
        conn.close()
    done = 0
    for i in ids:
        try:
            r = await write_from_ledger(svc, i)
            done += r["status"] == "written"
        except Exception:  # noqa: BLE001
            log.exception("retry of %s crashed", i)
    return done
