import json
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

SignIn = Callable[[str], None]


def _new_session(client: TestClient, workspace: str = "default") -> dict:
    response = client.post(
        "/api/sessions",
        json={"model_id": "test-model", "workspace": workspace, "autonomy": "ask"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _run_turn(ws: Any, text: str) -> list[dict]:
    ws.send_json({"type": "user", "text": text})
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] == "turn_end":
            return frames


def test_anonymous_callers_reach_nothing(client: TestClient) -> None:
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/models").status_code == 401
    assert (
        client.post("/api/sessions", json={"model_id": "test-model"}).status_code == 401
    )
    # The page redirects to the sign-in form instead of leaking the app shell.
    index = client.get("/", follow_redirects=False)
    assert index.status_code == 303 and index.headers["location"] == "/login"


def test_workspace_is_resolved_under_the_user_root_not_a_client_path(
    client: TestClient, sign_in: SignIn, tmp_path
) -> None:
    sign_in("alice")
    alice = client.get("/api/me").json()
    session = _new_session(client, "project-a")
    assert session["workspace"].endswith(f"{alice['id']}/project-a")
    assert (tmp_path / "workspaces" / alice["id"] / "project-a").is_dir()
    for attempt in ("../../etc", "/etc", "a/b"):
        response = client.post(
            "/api/sessions", json={"model_id": "test-model", "workspace": attempt}
        )
        assert response.status_code == 422, attempt
    assert client.get("/api/models").json()["recent_workspaces"] == ["project-a"]


def test_sessions_are_private_to_their_owner(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("alice")
    session = _new_session(client)
    sid = session["id"]
    sign_in("bob")
    assert client.get("/api/sessions").json()["sessions"] == []
    # Another member's session is indistinguishable from a missing one.
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.patch(f"/api/sessions/{sid}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/sessions/{sid}").status_code == 404
    assert client.post(f"/api/sessions/{sid}/fork", json={}).status_code == 404
    with client.websocket_connect(f"/ws?session={sid}") as ws:
        assert ws.receive_json()["type"] == "error"
    sign_in("alice")
    assert [s["id"] for s in client.get("/api/sessions").json()["sessions"]] == [sid]


def test_turn_bills_usage_and_reports_remaining_quota(
    client: TestClient, sign_in: SignIn, fake_backend: Any
) -> None:
    fake_backend.usage_frame = {
        "cumulative": True,
        "scope": "conversation",
        "input": 1_000_000,
        "output": 0,
        "models": [{"model": "gpt-test", "input": 1_000_000, "output": 0}],
    }
    sign_in("alice")
    session = _new_session(client)
    with client.websocket_connect(f"/ws?session={session['id']}") as ws:
        assert ws.receive_json()["type"] == "hello"
        frames = _run_turn(ws, "hi")
    usage = next(f for f in frames if f["type"] == "usage")
    # $5/Mtok fallback price; the agent's own estimate is replaced by ours.
    assert usage["charged_usd"] == "5.000000"
    assert usage["cost_usd"] == 5.0
    budget = next(w for w in usage["quota"] if w["window"] == "budget")
    assert budget["spent_usd"] == "5.000000"
    assert budget["remaining_usd"] == "15.000000"
    assert client.get("/api/me/quota").json()["windows"][0]["spent_usd"] == "5.000000"


def test_next_turn_is_refused_once_the_burst_window_is_spent(
    client: TestClient, sign_in: SignIn, fake_backend: Any
) -> None:
    fake_backend.usage_frame = {
        "cumulative": False,
        "scope": "request",
        "input": 1_000_000,
        "output": 0,
        "models": [{"model": "gpt-test", "input": 1_000_000, "output": 0}],
    }
    sign_in("alice")
    session = _new_session(client)
    with client.websocket_connect(f"/ws?session={session['id']}") as ws:
        assert ws.receive_json()["type"] == "hello"
        # The first turn runs to completion even though $5 exceeds the $3 burst
        # limit: an in-flight turn is never cut off.
        _run_turn(ws, "one")
        ws.send_json({"type": "user", "text": "two"})
        refusal = ws.receive_json()
        assert refusal["type"] == "quota_exceeded"
        assert "burst quota exhausted" in refusal["message"]
        assert refusal["retry_at"]
        assert ws.receive_json()["type"] == "turn_end"
    # The refused message never ran, so only the first exchange is stored.
    turns = client.get(f"/api/sessions/{session['id']}").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert json.dumps(turns).count('"two"') == 0


def test_one_tab_per_session_and_fork_stays_owner_scoped(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("alice")
    session = _new_session(client)
    with client.websocket_connect(f"/ws?session={session['id']}") as ws:
        assert ws.receive_json()["type"] == "hello"
        with client.websocket_connect(f"/ws?session={session['id']}") as second:
            assert second.receive_json()["type"] == "session_busy"
    fork = client.post(f"/api/sessions/{session['id']}/fork", json={})
    assert fork.status_code == 200, fork.text
    child = fork.json()
    assert child["parent_id"] == session["id"]
    assert {s["id"] for s in client.get("/api/sessions").json()["sessions"]} == {
        session["id"],
        child["id"],
    }
    sign_in("bob")
    assert client.get(f"/api/sessions/{child['id']}").status_code == 404
