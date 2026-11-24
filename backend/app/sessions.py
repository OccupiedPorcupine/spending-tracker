"""Server-side sessions. The cookie holds a random 256-bit ID; SQLite stores only its SHA-256.

A session ends after SESSION_IDLE_DAYS without use. With the "require Face ID every time" setting
on, it also ends after 5 minutes without a request.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass

from fastapi import Request, Response

from .config import settings
from .db import connect, get_setting, now_ms

STRICT_IDLE_MS = 5 * 60 * 1000
TOUCH_EVERY_MS = 30 * 1000


def cookie_name() -> str:
    return "__Host-spend" if cookie_secure() else "spend_session"


def cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "1") != "0"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class Session:
    id_hash: str
    credential_id: str | None
    created_at: int
    last_seen: int


def idle_limit_ms(conn: sqlite3.Connection) -> int:
    if get_setting(conn, "require_unlock", "0") == "1":
        return STRICT_IDLE_MS
    return settings().session_idle_days * 86_400_000


def current(request: Request) -> Session | None:
    raw = request.cookies.get(cookie_name())
    if not raw or len(raw) > 100:
        return None
    h = _hash(raw)
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id_hash = ?", (h,)).fetchone()
        if not row:
            return None
        now = now_ms()
        if now - row["last_seen"] > idle_limit_ms(conn):
            conn.execute("DELETE FROM sessions WHERE id_hash = ?", (h,))
            conn.commit()
            return None
        if now - row["last_seen"] > TOUCH_EVERY_MS:
            conn.execute("UPDATE sessions SET last_seen = ? WHERE id_hash = ?", (now, h))
            conn.commit()
        return Session(h, row["credential_id"], row["created_at"], now)
    finally:
        conn.close()


def create(conn: sqlite3.Connection, response: Response, credential_id: str, user_agent: str = "") -> None:
    raw = secrets.token_urlsafe(32)  # 256 bits
    now = now_ms()
    conn.execute(
        "INSERT INTO sessions (id_hash, credential_id, created_at, last_seen, user_agent) VALUES (?, ?, ?, ?, ?)",
        (_hash(raw), credential_id, now, now, user_agent[:200]),
    )
    # Expiry is enforced on the server; the cookie itself just lives as long as browsers allow.
    response.set_cookie(cookie_name(), raw, max_age=400 * 86400, path="/", secure=cookie_secure(),
                        httponly=True, samesite="strict")


def end(conn: sqlite3.Connection, request: Request, response: Response) -> None:
    raw = request.cookies.get(cookie_name())
    if raw:
        conn.execute("DELETE FROM sessions WHERE id_hash = ?", (_hash(raw),))
    response.delete_cookie(cookie_name(), path="/", secure=cookie_secure(), httponly=True, samesite="strict")


def prune(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sessions WHERE last_seen < ?", (now_ms() - settings().session_idle_days * 86_400_000,))
