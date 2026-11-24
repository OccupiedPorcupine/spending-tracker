"""SQLite access and migrations. Timestamps are integer epoch milliseconds (UTC)."""
import sqlite3
import time
from pathlib import Path
from typing import Iterator

from .config import settings

MIGRATIONS = Path(__file__).parent / "migrations"


def now_ms() -> int:
    return int(time.time() * 1000)


def connect() -> sqlite3.Connection:
    path = settings().db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply numbered .sql files above PRAGMA user_version. Returns the new version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for f in sorted(MIGRATIONS.glob("*.sql")):
        n = int(f.name.split("_", 1)[0])
        if n <= current:
            continue
        conn.executescript("BEGIN;\n" + f.read_text() + f"\nPRAGMA user_version = {n};\nCOMMIT;")
        current = n
    return current


def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now_ms()),
    )
