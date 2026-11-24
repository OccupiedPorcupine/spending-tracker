"""Which sheet the app writes to: "dev" (SHEET_ID_DEV) or "live" (SHEET_ID).

The choice is made on the settings page and stored in SQLite. SHEET_TARGET in .env is only the default
for a fresh database. Both sheet IDs still come from .env only, so the page can pick between them but
never point the app at any other sheet.
"""
from __future__ import annotations

import json
import os
import sqlite3

from .config import settings
from .db import connect, get_setting

TARGETS = ("dev", "live")


def current_target(conn: sqlite3.Connection | None = None) -> str:
    own = conn is None
    conn = conn or connect()
    try:
        t = get_setting(conn, "sheet_target")
    finally:
        if own:
            conn.close()
    return t if t in TARGETS else settings().sheet_target


def sheet_id_for(target: str) -> str:
    s = settings()
    return {"dev": s.sheet_id_dev, "live": s.sheet_id}.get(target, "")


def active_sheet_id(conn: sqlite3.Connection | None = None) -> str:
    return sheet_id_for(current_target(conn))


_fakes: dict[str, object] = {}


def backend_for(target: str):
    """A sheet backend for one target. SHEET_BACKEND=fake (local development) gives a fake per target."""
    s = settings()
    if os.environ.get("SHEET_BACKEND") == "fake":
        from .sheets import FakeSheet
        if target not in _fakes:
            f = FakeSheet.demo()
            f.title = "2026 Journal" if target == "live" else "2026 Journal (dev)"
            _fakes[target] = f
        return _fakes[target]
    sid = sheet_id_for(target)
    if not sid:
        raise RuntimeError(f"no sheet ID configured for {target!r}")
    from .sheets import GoogleSheet
    return GoogleSheet(sid, s.sheet_tab, s.google_sa_file)


def active_backend():
    return backend_for(current_target())


def service_account_email() -> str | None:
    """The address a sheet has to be shared with (shown on the settings page when access is missing)."""
    try:
        with open(settings().google_sa_file) as f:
            return json.load(f).get("client_email")
    except (OSError, ValueError):
        return None
