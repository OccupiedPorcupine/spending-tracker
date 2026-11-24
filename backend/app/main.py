import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, entries
from .config import settings
from .db import connect, migrate
from .security import gate
from .sheets import GoogleSheet
from .writer import SheetService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Never log request bodies; uvicorn's access log only has method, path and status.
STATIC = Path(os.environ.get("STATIC_DIR", Path(__file__).parent.parent / "static"))


def google_backend():
    s = settings()
    if os.environ.get("SHEET_BACKEND") == "fake":  # local development only
        from .sheets import FakeSheet
        return FakeSheet.demo()
    if not s.active_sheet_id:
        raise RuntimeError(f"no sheet ID configured for SHEET_TARGET={s.sheet_target}")
    return GoogleSheet(s.active_sheet_id, s.sheet_tab, s.google_sa_file)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = connect()
    migrate(conn)
    conn.close()
    if not hasattr(app.state, "sheet"):
        app.state.sheet = SheetService(google_backend)
    task = None
    if os.environ.get("RETRY_LOOP", "1") == "1":
        task = asyncio.create_task(entries.retry_loop(app.state.sheet))
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Spend", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(gate)
app.include_router(auth.router)
app.include_router(entries.router)


@app.get("/api/health")
def health():
    """Status only, no data. `sheets` reports the last Sheets call without making a new one."""
    conn = connect()
    try:
        v = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    h = app.state.sheet.health if hasattr(app.state, "sheet") else None
    return {"ok": True, "version": __version__, "schema": v, "commit": os.environ.get("GIT_COMMIT", "dev"),
            "target": settings().sheet_target,
            "sheets": {"last_ok": h.ok if h else None, "last_at": h.at if h else None}}


if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

NO_CACHE = {"Cache-Control": "no-cache"}


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Built frontend files; unknown non-API paths fall back to index.html for the client router.
    The shell holds no data (everything comes from session-protected /api), so it is served to
    anyone on the tailnet; that is what lets the lock screen and the offline form load."""
    if path.startswith("api/"):
        raise HTTPException(404, "not found")
    f = (STATIC / path).resolve()
    if path and f.is_file() and STATIC.resolve() in f.parents:
        return FileResponse(f, headers=NO_CACHE)
    index = STATIC / "index.html"
    if not index.is_file():
        raise HTTPException(404, "frontend not built")
    return FileResponse(index, headers=NO_CACHE)
