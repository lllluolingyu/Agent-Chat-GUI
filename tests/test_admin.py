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
