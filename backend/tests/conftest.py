import datetime as dt
import hashlib
import json
import os
import secrets
import struct
import uuid

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from webauthn.helpers import bytes_to_base64url, encode_cbor

from app.sheets import FakeSheet, to_serial

HOST = "spending.munyau.space"
ORIGIN = f"https://{HOST}"


def sample_rows():
    """A small sheet shaped like the real one: complete rows, then a run of amount-only rows."""
    d = lambda s: to_serial(dt.date.fromisoformat(s))  # noqa: E731
    rows = [
        [166.67, d("2026-11-01"), "Revenue", "Yes", "MYR", "Fees from Yuzuru"],
        [-4.5, d("2026-11-20"), "Food", "Yes", "SGD", "lunch"],
        [-12.0, d("2026-11-23"), "Things", "No", "SGD", "Grab"],
        [-1.4, d("2026-11-24"), "Food", "Yes", "SGD", "Kopi"],
        [-30.0, d("2026-11-24"), "Food", "Yes", "MYR", "dinner"],
        [-7.8],  # amount only
        [-1.6],
    ]
    return [r + [None] * (23 - len(r)) for r in rows]


@pytest.fixture
def sheet():
    return FakeSheet(sample_rows(), formula_rows=40)


@pytest.fixture
def client(tmp_path, monkeypatch, sheet):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("PUBLIC_HOST", HOST)
    monkeypatch.setenv("SHEET_ID_DEV", "dev-sheet")
    monkeypatch.setenv("RETRY_LOOP", "0")
    monkeypatch.delenv("DEV_ORIGINS", raising=False)
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr("app.writer.today", lambda: dt.date(2026, 11, 24))
    monkeypatch.setattr("app.entries.today", lambda: dt.date(2026, 11, 24))
    from app import auth
    from app.main import app
    from app.writer import SheetService
    auth.auth_limit.reset()
    app.state.sheet = SheetService(lambda: sheet)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
        c.sheet = sheet
        yield c
    del app.state.sheet


@pytest.fixture
def authed(client):
    """A client with a valid session, made directly in the database."""
    from app.db import connect, now_ms
    from app.sessions import _hash, cookie_name
    raw = secrets.token_urlsafe(32)
    conn = connect()
    conn.execute("INSERT INTO sessions (id_hash, credential_id, created_at, last_seen) VALUES (?, NULL, ?, ?)",
                 (_hash(raw), now_ms(), now_ms()))
    conn.commit()
    conn.close()
    client.cookies.set(cookie_name(), raw, domain=HOST)
    return client


def entry(**kw):
    base = {"id": str(uuid.uuid4()), "amount": 4.5, "direction": "expense", "category": "Food",
            "required": True, "currency": "SGD", "notes": "Chicken rice", "date": "2026-11-24"}
    return base | kw


class SoftAuthenticator:
    """Minimal platform authenticator: ES256, 'none' attestation, user verified."""

    def __init__(self, rp_id=HOST, origin=ORIGIN):
        self.rp_id, self.origin = rp_id, origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = secrets.token_bytes(16)
        self.count = 0
        self.user_handle = None

    def _client_data(self, typ, challenge):
        return json.dumps({"type": typ, "challenge": challenge, "origin": self.origin, "crossOrigin": False}).encode()

    def _auth_data(self, flags, attested=b""):
        self.count += 1
        return hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count) + attested

    def create(self, options, uv=True):
        self.user_handle = options["user"]["id"]
        nums = self.key.public_key().public_numbers()
        cose = encode_cbor({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        attested = b"\0" * 16 + struct.pack(">H", len(self.cred_id)) + self.cred_id + cose
        flags = 0x01 | (0x04 if uv else 0) | 0x40
        att = encode_cbor({"fmt": "none", "attStmt": {}, "authData": self._auth_data(flags, attested)})
        cid = bytes_to_base64url(self.cred_id)
        return {"id": cid, "rawId": cid, "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": bytes_to_base64url(self._client_data("webauthn.create", options["challenge"])),
                             "attestationObject": bytes_to_base64url(att), "transports": ["internal", "hybrid"]}}

    def get(self, options, uv=True):
        ad = self._auth_data(0x01 | (0x04 if uv else 0))
        cd = self._client_data("webauthn.get", options["challenge"])
        sig = self.key.sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        cid = bytes_to_base64url(self.cred_id)
        return {"id": cid, "rawId": cid, "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": bytes_to_base64url(cd), "authenticatorData": bytes_to_base64url(ad),
                             "signature": bytes_to_base64url(sig), "userHandle": self.user_handle}}
