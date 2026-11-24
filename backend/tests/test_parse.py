import asyncio
import datetime as dt
import json

import pytest

from app import parse
from app.security import RateLimit

TODAY = dt.date(2026, 11, 24)


@pytest.fixture
def ai(authed, monkeypatch):
    """An authed client whose OpenAI call is replaced by a stub that records what it was sent."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("app.parse.today", lambda: TODAY)
    monkeypatch.setattr(parse, "parse_limit", RateLimit(30, 60))
    sent = {}
    reply = {"items": [{"amount": 1.4, "direction": "expense", "category": "Food", "required": True,
                        "currency": "SGD", "notes": "kopi", "date": "2026-11-24"}]}

    async def fake(instructions, user_input, json_schema):
        sent.update(instructions=instructions, input=user_input, schema=json_schema)
        r = sent.get("reply", reply)
        if isinstance(r, BaseException):
            raise r
        if callable(r):
            return await r()
        return (r if isinstance(r, str) else json.dumps(r)), 900, 60

    monkeypatch.setattr(parse, "call_model", fake)
    authed.sent = sent
    return authed


def usage_rows():
    from app.db import connect
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM ai_usage ORDER BY id")]
    finally:
        conn.close()


def test_parse_returns_cleaned_draft(ai):
    r = ai.post("/api/parse", json={"text": "kopi 1.40"})
    assert r.status_code == 200, r.text
    (item,) = r.json()["items"]
    assert item == {"amount": 1.4, "direction": "expense", "category": "Food", "required": True,
                    "currency": "SGD", "notes": "Kopi", "date": "2026-11-24"}
    u = usage_rows()
    assert len(u) == 1 and u[0]["ok"] == 1 and u[0]["input_tokens"] == 900 and u[0]["items"] == 1


def test_parse_never_saves(ai):
    before = [list(r) for r in ai.sheet.grid(False)]
    ai.post("/api/parse", json={"text": "kopi 1.40"})
    from app.db import connect
    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    conn.close()
    assert ai.sheet.grid(False) == before


def test_openai_gets_only_text_date_currency_categories_and_notes(ai):
    ai.post("/api/parse", json={"text": "grab 12 rm"})
    sent = ai.sent["input"]
    assert '"""\ngrab 12 rm\n"""' in sent
    assert "Today: 2026-11-24 (Tuesday)" in sent
    assert "Default currency: SGD" in sent
    assert "- Kopi -> Food, required, SGD" in sent  # a recent note as an example
    # nothing else from the sheet: no amounts, no row dates, no totals
    for leak in ("166.67", "4.5", "12.0", "1.4", "30", "7.8", "1.6", "2026-11-20", "2026-11-23", "row 2", "Row"):
        assert leak not in sent.replace("grab 12 rm", ""), leak
    assert set(ai.sent["schema"]["properties"]["items"]["items"]["properties"]["category"]["enum"]) == \
        {"Food", "Things", "Revenue", "Loan"}


def test_examples_are_recent_distinct_notes_without_amounts(ai):
    from app.writer import recent_examples
    ex = recent_examples(ai.sheet.grid(False), 20)
    assert ex[0]["notes"] in ("Kopi", "dinner")  # the newest date first
    assert {e["notes"] for e in ex} >= {"Kopi", "dinner", "Grab", "lunch", "Fees from Yuzuru"}
    assert all(set(e) == {"notes", "category", "required", "currency"} for e in ex)


def test_timeout_falls_back(ai, monkeypatch):
    monkeypatch.setattr(parse, "TIMEOUT", 0.05)

    async def slow():
        await asyncio.sleep(2)
    ai.sent["reply"] = slow
    r = ai.post("/api/parse", json={"text": "kopi 1.40"})
    assert r.status_code == 502 and r.json()["reason"] == "timeout"
    assert usage_rows()[-1]["ok"] == 0 and usage_rows()[-1]["error"] == "timeout"


@pytest.mark.parametrize("reply", ["not json", {"items": []}, {"nope": 1}, RuntimeError("boom")])
def test_bad_replies_fall_back(ai, reply):
    ai.sent["reply"] = reply
    r = ai.post("/api/parse", json={"text": "kopi 1.40"})
    assert r.status_code == 502
    assert usage_rows()[-1]["ok"] == 0


def test_clean_rules():
    cats = ["Food", "Things", "Revenue", "Loan"]
    raw = {"items": [
        {"amount": 0, "direction": "expense", "category": "Nope", "required": False, "currency": "XYZ",
         "notes": "  lunch   with   team ", "date": "2099-01-01"},
        {"amount": 50, "direction": "income", "category": "Food", "required": False, "currency": "MYR",
         "notes": "fees from wade", "date": "2026-11-23"},
        {"amount": -3.456, "direction": "expense", "category": "Revenue", "required": True, "currency": "SGD",
         "notes": "x", "date": "bad"},
    ] + [{"amount": 1, "direction": "expense", "category": "Food", "required": True, "currency": "SGD",
          "notes": "n", "date": "2026-11-24"}] * 5}
    a, b, c, *rest = parse.clean(raw, cats, "SGD", TODAY)
    assert a == {"amount": None, "direction": "expense", "category": None, "required": False,
                 "currency": "SGD", "notes": "Lunch with team", "date": "2026-11-24"}
    assert b["category"] == "Revenue" and b["required"] is True and b["date"] == "2026-11-23"
    assert c["direction"] == "income" and c["amount"] == 3.46 and c["date"] == "2026-11-24"
    assert len(rest) == 2  # at most 5 items


def test_rate_limited(ai, monkeypatch):
    monkeypatch.setattr(parse, "parse_limit", RateLimit(2, 60))
    codes = [ai.post("/api/parse", json={"text": "kopi 1.40"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_not_configured(ai, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    assert ai.post("/api/parse", json={"text": "kopi"}).status_code == 503


def test_input_limits(ai):
    assert ai.post("/api/parse", json={"text": ""}).status_code == 422
    assert ai.post("/api/parse", json={"text": "   "}).status_code == 422
    assert ai.post("/api/parse", json={"text": "x" * 301}).status_code == 422


def test_needs_session_and_origin(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert client.post("/api/parse", json={"text": "kopi"}).status_code == 401
    assert client.post("/api/parse", json={"text": "kopi"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_status_reports_ai_usage(ai):
    ai.post("/api/parse", json={"text": "kopi 1.40"})
    a = ai.get("/api/status").json()["ai"]
    assert a["calls"] == 1 and a["ok"] == 1 and a["enabled"] is True
    assert a["cost_usd"] == pytest.approx((900 * 0.10 + 60 * 0.50) / 1e6, abs=1e-4)
