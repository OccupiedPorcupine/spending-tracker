"""Passkey login (WebAuthn) for one user.

- No open registration: `spend enroll` prints a one-time code (15 minutes, 5 wrong tries burn it).
  The code is needed to register a passkey, and is used up by a successful registration.
- Discoverable credentials and user verification required (Face ID or the device passcode).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from webauthn import (generate_authentication_options, generate_registration_options, options_to_json,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from . import sessions
from .config import settings
from .db import get_db, get_setting, now_ms, set_setting
from .security import RateLimit, allowed_origins, client_ip

log = logging.getLogger("spend.auth")
router = APIRouter(prefix="/api")

CODE_TTL_MS = 15 * 60 * 1000
CODE_MAX_FAILURES = 5
CHALLENGE_TTL_MS = 5 * 60 * 1000
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
auth_limit = RateLimit(20, 60)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").upper().encode()).hexdigest()


def new_enroll_code(conn: sqlite3.Connection) -> str:
    """Burn any unused code and issue a new one. Returns it formatted as XXXX-XXXX."""
    now = now_ms()
    conn.execute("UPDATE enroll_codes SET burned_at = ? WHERE used_at IS NULL AND burned_at IS NULL", (now,))
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    conn.execute("INSERT INTO enroll_codes (code_hash, created_at, expires_at) VALUES (?, ?, ?)",
                 (_hash_code(raw), now, now + CODE_TTL_MS))
    return f"{raw[:4]}-{raw[4:]}"


def _active_code(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM enroll_codes WHERE used_at IS NULL AND burned_at IS NULL AND expires_at > ? "
        "ORDER BY id DESC LIMIT 1", (now_ms(),)).fetchone()


def _limit(request: Request) -> None:
    if not auth_limit.check(client_ip(request)):
        raise HTTPException(429, "too many attempts, wait a minute")


def _user_handle(conn: sqlite3.Connection) -> bytes:
    h = get_setting(conn, "user_handle")
    if not h:
        h = bytes_to_base64url(secrets.token_bytes(32))
        set_setting(conn, "user_handle", h)
    return base64url_to_bytes(h)


def _store_challenge(conn: sqlite3.Connection, kind: str, challenge: bytes, code_id: int | None = None) -> str:
    conn.execute("DELETE FROM challenges WHERE created_at < ?", (now_ms() - CHALLENGE_TTL_MS,))
    cid = secrets.token_urlsafe(16)
    conn.execute("INSERT INTO challenges (id, kind, challenge, code_id, created_at) VALUES (?, ?, ?, ?, ?)",
                 (cid, kind, challenge, code_id, now_ms()))
    return cid


def _take_challenge(conn: sqlite3.Connection, cid: str, kind: str):
    row = conn.execute("SELECT * FROM challenges WHERE id = ? AND kind = ?", (cid, kind)).fetchone()
    conn.execute("DELETE FROM challenges WHERE id = ?", (cid,))
    conn.commit()  # single use even if verification then fails
    if not row or now_ms() - row["created_at"] > CHALLENGE_TTL_MS:
        raise HTTPException(400, "challenge expired, try again")
    return row


# ---------------------------------------------------------------------- state

@router.get("/auth/state")
def state(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    sess = sessions.current(request)
    n = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    return {"authenticated": sess is not None, "passkeys": n,
            "require_unlock": get_setting(conn, "require_unlock", "0") == "1"}


# ---------------------------------------------------------------------- registration

class RegisterStart(BaseModel):
    code: str = Field(min_length=4, max_length=20)


class RegisterFinish(BaseModel):
    challenge_id: str = Field(max_length=64)
    credential: dict
    name: str = Field(default="Passkey", max_length=40)


@router.post("/auth/register/options")
def register_options(body: RegisterStart, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    _limit(request)
    code = _active_code(conn)
    if not code:
        raise HTTPException(400, "no valid enrolment code; run `spend enroll` on mysites")
    if not hmac.compare_digest(code["code_hash"], _hash_code(body.code)):
        failures = code["failures"] + 1
        burned = now_ms() if failures >= CODE_MAX_FAILURES else None
        conn.execute("UPDATE enroll_codes SET failures = ?, burned_at = ? WHERE id = ?", (failures, burned, code["id"]))
        conn.commit()
        log.warning("wrong enrolment code (%d/%d)%s", failures, CODE_MAX_FAILURES, ", code burned" if burned else "")
        raise HTTPException(400, "wrong code" + (" - it is now burned; run `spend enroll` again" if burned else ""))
    s = settings()
    existing = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["id"]))
                for r in conn.execute("SELECT id FROM credentials")]
    opts = generate_registration_options(
        rp_id=s.rp_id, rp_name="Spend", user_id=_user_handle(conn), user_name="spend", user_display_name="Spend",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=existing,
    )
    cid = _store_challenge(conn, "register", opts.challenge, code["id"])
    return {"challenge_id": cid, "options": json.loads(options_to_json(opts))}


@router.post("/auth/register/verify")
def register_verify(body: RegisterFinish, request: Request, response: Response,
                    conn: sqlite3.Connection = Depends(get_db)):
    _limit(request)
    ch = _take_challenge(conn, body.challenge_id, "register")
    code = _active_code(conn)
    if not code or code["id"] != ch["code_id"]:
        raise HTTPException(400, "enrolment code expired or already used")
    s = settings()
    try:
        v = verify_registration_response(
            credential=body.credential, expected_challenge=ch["challenge"], expected_rp_id=s.rp_id,
            expected_origin=sorted(allowed_origins()), require_user_verification=True)
    except Exception as e:  # noqa: BLE001
        log.warning("passkey registration failed: %s", type(e).__name__)
        raise HTTPException(400, "passkey could not be verified") from None
    cred_id = bytes_to_base64url(v.credential_id)
    transports = (body.credential.get("response") or {}).get("transports") or []
    now = now_ms()
    conn.execute(
        "INSERT INTO credentials (id, public_key, sign_count, name, transports, aaguid, created_at, last_used) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cred_id, v.credential_public_key, v.sign_count, body.name.strip() or "Passkey",
         json.dumps(transports), str(v.aaguid), now, now))
    conn.execute("UPDATE enroll_codes SET used_at = ? WHERE id = ?", (now, code["id"]))
    sessions.create(conn, response, cred_id, request.headers.get("user-agent", ""))
    log.info("passkey registered: %s", cred_id[:8])
    return {"ok": True}


# ---------------------------------------------------------------------- login

class LoginFinish(BaseModel):
    challenge_id: str = Field(max_length=64)
    credential: dict


@router.post("/auth/login/options")
def login_options(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    _limit(request)
    opts = generate_authentication_options(rp_id=settings().rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    cid = _store_challenge(conn, "login", opts.challenge)
    return {"challenge_id": cid, "options": json.loads(options_to_json(opts))}


@router.post("/auth/login/verify")
def login_verify(body: LoginFinish, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    _limit(request)
    ch = _take_challenge(conn, body.challenge_id, "login")
    cred_id = str(body.credential.get("id", ""))[:512]
    row = conn.execute("SELECT * FROM credentials WHERE id = ?", (cred_id,)).fetchone()
    if not row:
        raise HTTPException(400, "this passkey is not registered (it may have been revoked)")
    s = settings()
    try:
        v = verify_authentication_response(
            credential=body.credential, expected_challenge=ch["challenge"], expected_rp_id=s.rp_id,
            expected_origin=sorted(allowed_origins()), credential_public_key=row["public_key"],
            credential_current_sign_count=row["sign_count"], require_user_verification=True)
    except Exception as e:  # noqa: BLE001
        log.warning("passkey login failed: %s", type(e).__name__)
        raise HTTPException(400, "passkey could not be verified") from None
    conn.execute("UPDATE credentials SET sign_count = ?, last_used = ? WHERE id = ?",
                 (v.new_sign_count, now_ms(), cred_id))
    sessions.prune(conn)
    sessions.create(conn, response, cred_id, request.headers.get("user-agent", ""))
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    sessions.end(conn, request, response)
    return {"ok": True}


# ---------------------------------------------------------------------- passkey management (session required)

@router.get("/passkeys")
def list_passkeys(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    mine = request.state.session.credential_id
    return {"passkeys": [
        {"id": r["id"], "name": r["name"], "created_at": r["created_at"], "last_used": r["last_used"],
         "this_device": r["id"] == mine}
        for r in conn.execute("SELECT * FROM credentials ORDER BY created_at")]}


@router.post("/passkeys/{cred_id}/revoke")
def revoke_passkey(cred_id: str, conn: sqlite3.Connection = Depends(get_db)):
    cur = conn.execute("DELETE FROM credentials WHERE id = ?", (cred_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "no such passkey")
    log.info("passkey revoked: %s", cred_id[:8])
    return {"ok": True}
