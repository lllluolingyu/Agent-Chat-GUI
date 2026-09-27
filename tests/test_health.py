from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentchat.app import default_data_dir


def test_health_and_placeholder(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "Agent Chat" in client.get("/").text


def test_data_dir_follows_the_deploy_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AGENT_CHAT_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_data_dir() == tmp_path / "agent-chat"
    monkeypatch.setenv("AGENT_CHAT_DATA_DIR", str(tmp_path / "state"))
    assert default_data_dir() == tmp_path / "state"
