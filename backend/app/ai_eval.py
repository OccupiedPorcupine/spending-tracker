"""`spend ai-eval < evals/ai_phrases.json`: score the AI parser on a phrase set, using the real model,
the sheet's real categories and recent-note examples, exactly as /api/parse does.

A phrase passes when it gives the expected number of items and every item has the expected category and
currency (the acceptance bar is 90%). Amount, direction and date are scored separately. Calls count
towards the month's AI usage like any other.
"""
from __future__ import annotations

import datetime as dt
import statistics

from . import parse
from .main import google_backend
from .writer import SheetService, recent_examples, today


async def evaluate(phrases: list[dict], currency: str = "SGD") -> int:
    svc = SheetService(google_backend)
    categories = await svc.categories()
    examples = recent_examples(await svc.values(0), 20)
    on = today()
    print(f"model {parse.settings().openai_model} | today {on} | default {currency} | "
          f"{len(categories)} categories | {len(examples)} examples\n")

    passed = 0
    fields = {"amount": [0, 0], "direction": [0, 0], "date": [0, 0]}
    times: list[int] = []
    for p in phrases:
        want = p["expect"]
        try:
            got, ms = await parse.run(p["text"], categories, examples, currency, on)
        except parse.ParseFailed as e:
            print(f"FAIL  {p['text']!r}: {e}")
            continue
        times.append(ms)
        ok = len(got) == len(want) and all(
            g["category"] == w["category"] and g["currency"] == w["currency"] for g, w in zip(got, want))
        passed += ok
        for g, w in zip(got, want):
            checks = {
                "amount": g["amount"] is not None and abs(g["amount"] - w["amount"]) < 0.005,
                "direction": g["direction"] == w.get("direction", "expense"),
                "date": g["date"] == (on - dt.timedelta(days=w.get("days_ago", 0))).isoformat(),
            }
            for k, v in checks.items():
                fields[k][0] += v
                fields[k][1] += 1
        shown = " + ".join(
            f"{g['notes']!r} {g['amount']} {g['currency']} {g['category']}"
            f"{'' if g['required'] else ' extra'}{' income' if g['direction'] == 'income' else ''}"
            f"{'' if g['date'] == on.isoformat() else ' ' + g['date']}" for g in got)
        print(f"{'ok  ' if ok else 'MISS'}  {ms:>5} ms  {p['text']!r:<40} -> {shown}")

    n = len(phrases)
    print(f"\ncategory + currency: {passed}/{n} = {passed / n:.0%} (bar: 90%)")
    for k, (good, total) in fields.items():
        if total:
            print(f"{k}: {good}/{total} items")
    if times:
        print(f"parse time: median {statistics.median(times):.0f} ms, max {max(times)} ms (bar: median < 3000)")
    return 0 if passed / n >= 0.9 and times and statistics.median(times) < 3000 else 1
