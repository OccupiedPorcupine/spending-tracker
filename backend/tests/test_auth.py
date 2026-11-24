from app.db import connect, now_ms
from conftest import SoftAuthenticator


def enroll_code():
    from app.auth import new_enroll_code
    conn = connect()
    code = new_enroll_code(conn)
    conn.commit()
    conn.close()
    return code


def register(client, code, auth=None, name="iPhone"):
    auth = auth or SoftAuthenticator()
    r = client.post("/api/auth/register/options", json={"code": code})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["options"]["authenticatorSelection"]["residentKey"] == "required"
    assert o["options"]["authenticatorSelection"]["userVerification"] == "required"
    r = client.post("/api/auth/register/verify",
                    json={"challenge_id": o["challenge_id"], "credential": auth.create(o["options"]), "name": name})
    return auth, r


def login(client, auth, uv=True):
    o = client.post("/api/auth/login/options", json={}).json()
    assert o["options"]["userVerification"] == "required"
    assert o["options"].get("allowCredentials", []) == []  # discoverable: no username step
    return client.post("/api/auth/login/verify", json={"challenge_id": o["challenge_id"], "credential": auth.get(o["options"], uv)})


def test_register_then_login(client):
    code = enroll_code()
    auth, r = register(client, code.lower())  # case and dash do not matter
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/state").json()["authenticated"] is True
    assert client.get("/api/settings").status_code == 200
    client.post("/api/auth/logout", json={})
    assert client.get("/api/settings").status_code == 401
    r = login(client, auth)
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").status_code == 200
    keys = client.get("/api/passkeys").json()["passkeys"]
    assert len(keys) == 1 and keys[0]["name"] == "iPhone" and keys[0]["this_device"]


def test_cookie_flags(client):
    _, r = register(client, enroll_code())
    c = r.headers["set-cookie"]
    assert c.startswith("__Host-spend=")
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert flag.lower() in c.lower()
    assert len(c.split(";")[0].split("=", 1)[1]) >= 43  # 32 random bytes, base64url


def test_registration_needs_a_code(client):
    r = client.post("/api/auth/register/options", json={"code": "ABCD-EFGH"})
    assert r.status_code == 400


def test_code_is_single_use(client):
    code = enroll_code()
    assert register(client, code)[1].status_code == 200
    client.cookies.clear()
    r = client.post("/api/auth/register/options", json={"code": code})
    assert r.status_code == 400


def test_expired_code_fails(client):
    code = enroll_code()
    conn = connect()
    conn.execute("UPDATE enroll_codes SET expires_at = ?", (now_ms() - 1,))
    conn.commit()
    conn.close()
    assert client.post("/api/auth/register/options", json={"code": code}).status_code == 400


def test_five_wrong_tries_burn_the_code(client):
    code = enroll_code()
    for i in range(5):
        r = client.post("/api/auth/register/options", json={"code": "WRNG-CODE"})
        assert r.status_code == 400
    assert "burned" in r.json()["detail"]
    assert client.post("/api/auth/register/options", json={"code": code}).status_code == 400


def test_new_code_burns_the_old_one(client):
    old = enroll_code()
    new = enroll_code()
    assert client.post("/api/auth/register/options", json={"code": old}).status_code == 400
    assert client.post("/api/auth/register/options", json={"code": new}).status_code == 200


def test_user_verification_is_required(client):
    code = enroll_code()
    o = client.post("/api/auth/register/options", json={"code": code}).json()
    a = SoftAuthenticator()
    r = client.post("/api/auth/register/verify", json={"challenge_id": o["challenge_id"], "credential": a.create(o["options"], uv=False)})
    assert r.status_code == 400
    # and on login
    a, r = register(client, enroll_code())
    client.post("/api/auth/logout", json={})
    assert login(client, a, uv=False).status_code == 400


def test_wrong_origin_in_client_data(client):
    code = enroll_code()
    o = client.post("/api/auth/register/options", json={"code": code}).json()
    a = SoftAuthenticator(origin="https://evil.example")
    r = client.post("/api/auth/register/verify", json={"challenge_id": o["challenge_id"], "credential": a.create(o["options"])})
    assert r.status_code == 400


def test_challenge_is_single_use(client):
    a, _ = register(client, enroll_code())
    client.post("/api/auth/logout", json={})
    o = client.post("/api/auth/login/options", json={}).json()
    cred = a.get(o["options"])
    assert client.post("/api/auth/login/verify", json={"challenge_id": o["challenge_id"], "credential": cred}).status_code == 200
    client.post("/api/auth/logout", json={})
    assert client.post("/api/auth/login/verify", json={"challenge_id": o["challenge_id"], "credential": cred}).status_code == 400


def test_revoked_passkey_cannot_log_in(client):
    a, _ = register(client, enroll_code(), name="Laptop")
    cid = client.get("/api/passkeys").json()["passkeys"][0]["id"]
    assert client.post(f"/api/passkeys/{cid}/revoke", json={}).status_code == 200
    # revoking also ended the sessions made with it
    assert client.get("/api/settings").status_code == 401
    assert login(client, a).status_code == 400


def test_idle_expiry(client, monkeypatch):
    register(client, enroll_code())
    conn = connect()
    conn.execute("UPDATE sessions SET last_seen = ?", (now_ms() - 31 * 86_400_000,))
    conn.commit()
    conn.close()
    assert client.get("/api/settings").status_code == 401


def test_strict_unlock_setting_expires_after_5_minutes(client):
    register(client, enroll_code())
    assert client.post("/api/settings", json={"require_unlock": True}).json()["require_unlock"] is True
    conn = connect()
    conn.execute("UPDATE sessions SET last_seen = ?", (now_ms() - 6 * 60_000,))
    conn.commit()
    conn.close()
    assert client.get("/api/settings").status_code == 401


def test_auth_rate_limit(client):
    codes = [client.post("/api/auth/login/options", json={}).status_code for _ in range(25)]
    assert 429 in codes
