"""Favourites: one-tap presets for entries made often ("Kopi, 1.40, Food, SGD"). Stored in SQLite only."""
from __future__ import annotations

import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .db import get_db, now_ms

router = APIRouter(prefix="/api")
MAX_FAVOURITES = 20


class Favourite(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    amount: float | None = Field(default=None, gt=0, le=1_000_000)  # none: the amount is typed each time
    direction: Literal["expense", "income"] = "expense"
    category: str = Field(min_length=1, max_length=40)
    required: bool = True
    currency: Literal["SGD", "MYR"]
    notes: str = Field(default="", max_length=120)

    @field_validator("name", "notes")
    @classmethod
    def _squash(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("amount")
    @classmethod
    def _cents(cls, v: float | None) -> float | None:
        if v is not None and abs(round(v, 2) - v) > 1e-9:
            raise ValueError("at most 2 decimal places")
        return None if v is None else round(v, 2)


class Empty(BaseModel):
    pass


def list_favourites(conn: sqlite3.Connection) -> list[dict]:
    return [{"id": r["id"], "name": r["name"], **json.loads(r["payload"])}
            for r in conn.execute("SELECT * FROM favourites ORDER BY position, id")]


@router.get("/favourites")
def get_favourites(conn: sqlite3.Connection = Depends(get_db)):
    return {"favourites": list_favourites(conn)}


@router.post("/favourites")
async def add_favourite(fav: Favourite, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if fav.category not in await request.app.state.sheet.categories():
        raise HTTPException(422, f"unknown category {fav.category!r}")
    if fav.direction == "income" and fav.category not in ("Revenue", "Loan"):
        raise HTTPException(422, "income goes under Revenue or Loan")
    have = conn.execute("SELECT COUNT(*) FROM favourites").fetchone()[0]
    if have >= MAX_FAVOURITES:
        raise HTTPException(409, f"you already have {MAX_FAVOURITES} favourites; remove one in settings first")
    payload = fav.model_dump(exclude={"name"})
    same = conn.execute("SELECT id FROM favourites WHERE name = ? AND payload = ?", (fav.name, json.dumps(payload))).fetchone()
    if same is None:  # saving the same preset twice keeps one
        pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM favourites").fetchone()[0]
        conn.execute("INSERT INTO favourites (name, payload, position, created_at) VALUES (?, ?, ?, ?)",
                     (fav.name, json.dumps(payload), pos, now_ms()))
    return {"favourites": list_favourites(conn)}


@router.post("/favourites/{fav_id}/delete")
def delete_favourite(fav_id: int, _: Empty, conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("DELETE FROM favourites WHERE id = ?", (fav_id,))
    return {"favourites": list_favourites(conn)}
