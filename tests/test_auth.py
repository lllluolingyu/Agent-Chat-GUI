from collections.abc import Callable

from fastapi.testclient import TestClient

from agentchat.app import SESSION_COOKIE
from agentchat.auth import hash_password, verify_password

SignIn = Callable[[str], None]


def test_password_hash_is_salted_and_verifies() -> None:
    first, second = hash_password("correct horse"), hash_password("correct horse")
    assert first != second
    assert verify_password("correct horse", first)
    assert not verify_password("wrong horse", first)
    assert not verify_password("correct horse", "not-a-hash")


def test_login_sets_http_only_cookie_and_logout_revokes_it(client: TestClient) -> None:
    assert client.get("/api/me").status_code == 401
    response = client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password"}
    )
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get("/api/me").json()["username"] == "alice"
    token = client.cookies[SESSION_COOKIE]
    client.post("/api/auth/logout")
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/me").status_code == 401


def test_session_cookie_is_secure_only_over_https(client: TestClient) -> None:
    body = {"username": "alice", "password": "alice-password"}
    # Plain http, as on a LAN: a Secure cookie would be dropped by the browser.
    plain = client.post("/api/auth/login", json=body)
    assert "Secure" not in plain.headers["set-cookie"]
    tls = client.post("https://testserver/api/auth/login", json=body)
    assert "Secure" in tls.headers["set-cookie"]


def test_login_rejects_bad_password_and_unknown_user(client: TestClient) -> None:
    for username, password in [("alice", "nope-nope"), ("nobody", "whatever1")]:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "invalid username or password"


def test_cross_origin_writes_are_refused(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "alice-password"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "alice-password"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200


def test_members_cannot_manage_users(client: TestClient, sign_in: SignIn) -> None:
    sign_in("alice")
    assert client.get("/api/admin/users").status_code == 403
    response = client.post(
        "/api/admin/users", json={"username": "bob", "password": "bob-password"}
    )
    assert response.status_code == 403


def test_admin_manages_users_and_disabling_ends_logins(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("alice")
    alice_token = client.cookies[SESSION_COOKIE]
    sign_in("admin")
    users = {u["username"]: u for u in client.get("/api/admin/users").json()["users"]}
    assert set(users) == {"admin", "alice", "bob"}

    created = client.post(
        "/api/admin/users", json={"username": "dana", "password": "dana-password"}
    )
    assert created.json()["role"] == "member"
    # Usernames are compared case-insensitively.
    duplicate = client.post(
        "/api/admin/users", json={"username": "DANA", "password": "dana-password"}
    )
    assert duplicate.status_code == 422
    short = client.post(
        "/api/admin/users", json={"username": "carol", "password": "short"}
    )
    assert short.status_code == 422

    patched = client.patch(
        f"/api/admin/users/{users['alice']['id']}", json={"active": False}
    )
    assert patched.json()["active"] is False
    client.cookies.set(SESSION_COOKIE, alice_token)
    assert client.get("/api/me").status_code == 401


def test_last_active_admin_cannot_be_removed(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("admin")
    admin_id = client.get("/api/me").json()["id"]
    for body in ({"active": False}, {"role": "member"}):
        response = client.patch(f"/api/admin/users/{admin_id}", json=body)
        assert response.status_code == 422
        assert "admin" in response.json()["detail"]
