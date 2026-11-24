from conftest import HOST, ORIGIN, entry


def test_health_is_open_and_has_no_data(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"ok", "version", "schema", "commit", "target", "sheets"}
    assert set(body["sheets"]) == {"last_ok", "last_at"}


def test_health_on_localhost_but_nothing_else(client):
    assert client.get("http://127.0.0.1:8000/api/health").status_code == 200
    assert client.get("http://127.0.0.1:8000/api/recent").status_code == 421
    assert client.get("http://127.0.0.1:8000/").status_code == 421


def test_unknown_host_is_refused(client):
    # DNS rebinding: an attacker's name pointing at the server
    r = client.get("/api/health", headers={"Host": "evil.example"})
    assert r.status_code == 421
    r = client.get("/", headers={"Host": "10.10.10.102"})
    assert r.status_code == 421


def test_protected_routes_need_a_session(client):
    for path in ("/api/recent", "/api/meta", "/api/status", "/api/settings", "/api/passkeys"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/entries", json=entry()).status_code == 401
    assert client.post("/api/entries/x/delete", json={}).status_code == 401


def test_writes_need_exact_origin(authed):
    e = entry()
    assert authed.post("/api/entries", json=e, headers={"Origin": "https://evil.example"}).status_code == 403
    assert authed.post("/api/entries", json=e, headers={"Origin": "http://" + HOST}).status_code == 403
    assert authed.post("/api/entries", json=e, headers={"Origin": ORIGIN + ".evil.example"}).status_code == 403
    r = authed.post("/api/entries", json=e, headers={"Origin": ""})
    assert r.status_code == 403
    assert authed.post("/api/entries", json=e).status_code == 200


def test_writes_need_json(authed):
    r = authed.post("/api/entries", content="id=1", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    r = authed.post("/api/entries", content="{}", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_auth_endpoints_also_check_origin(client):
    assert client.post("/api/auth/login/options", json={}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_no_cors_and_no_preflight(authed):
    r = authed.options("/api/entries", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.status_code == 405
    r = authed.get("/api/recent", headers={"Origin": "https://evil.example"})
    assert not any(h.lower().startswith("access-control-") for h in r.headers)


def test_security_headers(client):
    r = client.get("/api/health")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_get_never_writes(authed):
    # GET on a write route is not a way in
    assert authed.get("/api/entries").status_code in (404, 405)
    assert authed.get("/api/auth/logout").status_code in (404, 405)
