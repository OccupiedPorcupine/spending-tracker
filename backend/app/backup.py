"""Nightly backup: `python -m app.backup [dir] [keep]`. Uses SQLite's online backup API,
so it is safe while the app is running. Keeps the newest `keep` copies."""
import sqlite3
import sys
import time
from pathlib import Path

from .config import settings


def run(dest_dir: str = "/backups", keep: int = 14) -> Path:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"spend-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(settings().db_path)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if ok != "ok":
        out.unlink(missing_ok=True)
        raise SystemExit(f"backup failed integrity check: {ok}")
    for old in sorted(dest.glob("spend-*.db"))[:-keep]:
        old.unlink()
    return out


if __name__ == "__main__":
    path = run(sys.argv[1] if len(sys.argv) > 1 else "/backups", int(sys.argv[2]) if len(sys.argv) > 2 else 14)
    print(f"backup written: {path} ({path.stat().st_size} bytes)")
