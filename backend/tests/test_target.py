"""Switching between the dev copy and the live sheet from the settings page."""
import json

import pytest

from app.db import connect, now_ms
from app.sheets import FakeSheet

from conftest import entry, sample_rows


@pytest.fixture
def two(authed, monkeypatch):
    """An authed client with a dev and a live fake sheet, served according to the current target."""
    monkeypatch.setenv("SHEET_ID", "live-sheet")
    from app.main import app
    from app.target import current_target
    from app.writer import SheetService
    sheets = {"dev": authed.sheet, "live": FakeSheet(sample_rows(), formula_rows=40, title="2026 Journal")}
    monkeypatch.setattr("app.entries.backend_for", lambda t: sheets[t])
    app.state.sheet = SheetService(lambda: sheets[current_target()])
    authed.sheets = sheets
    return authed


def ids_in(sheet):
    return {r[6] for r in sheet.grid(False) if r[6]}


def test_defaults_to_env_target(two):
    s = two.get("/api/settings").json()
    assert s["sheet_target"] == "dev" and s["targets"] == {"dev": True, "live": True}


def test_switch_sends_new_entries_to_live(two):
    e1 = entry()
    assert two.post("/api/entries", json=e1).json()["status"] == "written"
    r = two.post("/api/settings", json={"sheet_target": "live"})
    assert r.status_code == 200 and r.json()["sheet_target"] == "live"
    e2 = entry(notes="Grab", category="Things")
    assert two.post("/api/entries", json=e2).json()["status"] == "written"
    assert e1["id"] in ids_in(two.sheets["dev"]) and e1["id"] not in ids_in(two.sheets["live"])
    assert e2["id"] in ids_in(two.sheets["live"]) and e2["id"] not in ids_in(two.sheets["dev"])
    assert two.get("/api/status").json()["sheet"] == {"title": "2026 Journal", "target": "live",
                                                     "locale": "en_US", "time_zone": "Asia/Singapore"}
    assert two.get("/api/meta").json()["target"] == "live"
    assert two.get("/api/health").json()["target"] == "live"
    conn = connect()
    assert conn.execute("SELECT sheet_id FROM entries WHERE id = ?", (e2["id"],)).fetchone()[0] == "live-sheet"
    conn.close()
    # and back again
    assert two.post("/api/settings", json={"sheet_target": "dev"}).json()["sheet_target"] == "dev"
    assert two.get("/api/meta").json()["target"] == "dev"


def test_undo_refuses_an_entry_in_the_other_sheet(two):
    e1 = entry()
    two.post("/api/entries", json=e1)
    two.post("/api/settings", json={"sheet_target": "live"})
    r = two.post(f"/api/entries/{e1['id']}/delete", json={})
    assert r.status_code == 409 and "other sheet" in r.json()["detail"]
    assert e1["id"] in ids_in(two.sheets["dev"])  # untouched
    two.post("/api/settings", json={"sheet_target": "dev"})
    assert two.post(f"/api/entries/{e1['id']}/delete", json={}).json()["status"] == "deleted"
    assert e1["id"] not in ids_in(two.sheets["dev"])


def test_switch_refused_when_the_sheet_cannot_be_opened(two, monkeypatch):
    def boom(t):
        raise PermissionError("403")
    monkeypatch.setattr("app.entries.backend_for", boom)
    monkeypatch.setattr("app.entries.service_account_email", lambda: "writer@example.iam.gserviceaccount.com")
    r = two.post("/api/settings", json={"sheet_target": "live"})
    assert r.status_code == 409 and "writer@example.iam.gserviceaccount.com" in r.json()["detail"]
    assert two.get("/api/settings").json()["sheet_target"] == "dev"


def test_switch_refused_while_entries_are_waiting(two):
    conn = connect()
    conn.execute("INSERT INTO entries (id, payload, status, sheet_id, created_at, updated_at) VALUES (?, ?, 'failed', ?, ?, ?)",
                 ("00000000-0000-4000-8000-000000000001", json.dumps(entry()), "dev-sheet", now_ms(), now_ms()))
    conn.commit()
    conn.close()
    r = two.post("/api/settings", json={"sheet_target": "live"})
    assert r.status_code == 409 and "1 entries" in r.json()["detail"]
    assert two.get("/api/settings").json()["sheet_target"] == "dev"


def test_switch_refused_without_a_live_id(two, monkeypatch):
    monkeypatch.setenv("SHEET_ID", "")
    assert two.get("/api/settings").json()["targets"]["live"] is False
    assert two.post("/api/settings", json={"sheet_target": "live"}).status_code == 409


def test_bad_target_rejected(two):
    assert two.post("/api/settings", json={"sheet_target": "prod"}).status_code == 422


def test_entry_queued_for_the_other_sheet_is_never_written_here(two):
    import asyncio

    from app.entries import write_from_ledger
    from app.main import app
    e = entry()
    conn = connect()
    conn.execute("INSERT INTO entries (id, payload, status, sheet_id, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?)",
                 (e["id"], json.dumps(e), "live-sheet", now_ms(), now_ms()))
    conn.commit()
    conn.close()
    res = asyncio.run(write_from_ledger(app.state.sheet, e["id"]))
    assert res["status"] == "failed" and res["retry"] is False
    assert e["id"] not in ids_in(two.sheets["dev"]) and e["id"] not in ids_in(two.sheets["live"])


def test_switch_needs_a_session(client):
    assert client.post("/api/settings", json={"sheet_target": "live"}).status_code == 401
