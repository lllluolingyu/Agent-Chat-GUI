from fastapi.testclient import TestClient


def test_health_and_placeholder(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "Agent Chat" in client.get("/").text
