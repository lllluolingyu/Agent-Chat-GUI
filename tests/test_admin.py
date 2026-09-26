from collections.abc import Callable

from fastapi.testclient import TestClient

SignIn = Callable[[str], None]


def test_members_cannot_read_or_change_quota_policy(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("alice")
    alice = client.get("/api/me").json()["id"]
    assert client.get("/api/admin/users").status_code == 403
    assert client.put(f"/api/admin/users/{alice}/quota", json={}).status_code == 403
    assert (
        client.post(
            f"/api/admin/users/{alice}/credit", json={"amount_usd": "5"}
        ).status_code
        == 403
    )
    assert client.get("/api/admin/usage").status_code == 403
    assert client.get("/api/doctor").status_code == 403
    # A member still sees their own limits.
    assert client.get("/api/me/quota").json()["policy"]["budget_usd"] == "20.000000"


def test_admin_sets_a_policy_that_takes_effect_for_the_member(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("alice")
    alice = client.get("/api/me").json()["id"]
    sign_in("admin")
    listed = client.get("/api/admin/users").json()["users"]
    assert {u["username"] for u in listed} == {"admin", "alice", "bob"}
    assert listed[0]["quota"]["policy"]["burst_usd"] == "3.000000"

    response = client.put(
        f"/api/admin/users/{alice}/quota",
        json={
            "budget_usd": "1.50",
            "budget_window_hours": 48,
            "burst_usd": None,
            "burst_window_hours": 2,
        },
    )
    assert response.json()["policy"] == {
        "budget_usd": "1.500000",
        "budget_window_hours": 48,
        "burst_usd": None,
        "burst_window_hours": 2,
    }
    sign_in("alice")
    quota = client.get("/api/me/quota").json()
    assert quota["policy"]["budget_window_hours"] == 48
    assert quota["windows"][1]["limit_usd"] is None


def test_credit_offsets_spend_and_shows_in_the_ledger(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("admin")
    users = {
        u["username"]: u["id"] for u in client.get("/api/admin/users").json()["users"]
    }
    credited = client.post(
        f"/api/admin/users/{users['alice']}/credit",
        json={"amount_usd": "2.50", "note": "family pot"},
    )
    assert credited.json()["credited_usd"] == "2.500000"
    budget = next(w for w in credited.json()["windows"] if w["window"] == "budget")
    # A credit is negative spend, so it raises the remaining balance.
    assert budget["remaining_usd"] == "22.500000"
    entry = client.get("/api/admin/usage").json()["entries"][0]
    assert entry["username"] == "alice" and entry["cost_usd"] == "-2.500000"
    assert entry["pricing"] == "adjustment" and entry["note"] == "family pot"


def test_invalid_quota_input_is_rejected(client: TestClient, sign_in: SignIn) -> None:
    sign_in("admin")
    users = {
        u["username"]: u["id"] for u in client.get("/api/admin/users").json()["users"]
    }
    alice = users["alice"]
    assert (
        client.put(
            f"/api/admin/users/{alice}/quota", json={"budget_usd": "abc"}
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/admin/users/{alice}/quota",
            json={"budget_usd": "1", "budget_window_hours": 0},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/admin/users/{alice}/credit", json={"amount_usd": "-1"}
        ).status_code
        == 422
    )
    assert client.put("/api/admin/users/nobody/quota", json={}).status_code == 404


def test_admin_creates_a_user_who_can_then_sign_in(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("admin")
    created = client.post(
        "/api/admin/users",
        json={"username": "erin", "password": "correct horse", "role": "member"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "member" and created.json()["active"] is True
    # A short password is refused rather than stored.
    assert (
        client.post(
            "/api/admin/users", json={"username": "frank", "password": "short"}
        ).status_code
        == 422
    )
    client.post("/api/auth/logout")
    login = client.post(
        "/api/auth/login", json={"username": "erin", "password": "correct horse"}
    )
    assert login.status_code == 200
    # The new member gets the default policy without an explicit one.
    assert client.get("/api/me/quota").json()["policy"]["budget_usd"] == "20.000000"


def test_usage_ledger_can_be_filtered_to_one_user(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("admin")
    users = {
        u["username"]: u["id"] for u in client.get("/api/admin/users").json()["users"]
    }
    for name in ("alice", "bob"):
        client.post(f"/api/admin/users/{users[name]}/credit", json={"amount_usd": "1"})
    everyone = client.get("/api/admin/usage").json()["entries"]
    assert {e["username"] for e in everyone} == {"alice", "bob"}
    only = client.get(f"/api/admin/usage?user_id={users['bob']}").json()["entries"]
    assert [e["username"] for e in only] == ["bob"]


def test_price_table_is_readable_by_an_admin_only(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("alice")
    assert client.get("/api/admin/prices").status_code == 403
    sign_in("admin")
    prices = client.get("/api/admin/prices").json()
    assert prices["path"].endswith("prices.toml")
    assert prices["fallback"]["input"] == "5.0"
    listed = {row["model"] for row in prices["models"]}
    # The shipped table covers the agents this product actually drives.
    assert {"claude-opus-5-5", "gpt-5.3-codex", "gpt-6-astra"} <= listed
