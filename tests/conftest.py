from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from agentgui.backends.base import Capabilities
from agentgui.catalog import Catalog, ModelEntry
from fastapi.testclient import TestClient

from agentchat.app import Settings, create_app
from agentchat.domain import UserRole

SignIn = Callable[[str], None]


class FakeBackend:
    """Stands in for an agent CLI: no process, one scripted usage frame."""

    capabilities = Capabilities(fork=True, images=True, files=True)
    usage_frame: dict | None = None

    def __init__(self, rec, store, profiles) -> None:
        self.session, self.store = rec, store

    async def start(self, session, approve) -> None:
        self.session = session

    async def run_turn(self, inp):
        from agentgui.protocol import frame

        yield frame("text", delta="ok")
        if self.usage_frame is not None:
            yield frame("usage", **self.usage_frame)
        yield frame("final", content="ok")

    async def stop(self):
        return []

    async def edit(self, seq, text):
        yield  # pragma: no cover - not exercised

    async def fork(self, through_seq):
        return self.store.clone(self.session, "native-fork")

    def reconcile(self, status) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_backend() -> Iterator[type[FakeBackend]]:
    """The scripted backend class; its usage frame resets between tests.

    Yielded as a fixture because this project must never import from `tests.`:
    the editable LingChat install owns that package name on sys.path.
    """
    FakeBackend.usage_frame = None
    yield FakeBackend
    FakeBackend.usage_frame = None


@pytest.fixture
def catalog() -> Catalog:
    return Catalog([ModelEntry("test-model", "Test model", "codex", "gpt-test")])


@pytest.fixture
def client(tmp_path: Path, catalog: Catalog) -> Iterator[TestClient]:
    app = create_app(
        Settings(tmp_path),
        catalog=catalog,
        backend_factory=lambda rec, store, profiles: FakeBackend(rec, store, profiles),
    )
    app.state.auth.create_user("admin", "admin-password", UserRole.ADMIN)
    app.state.auth.create_user("alice", "alice-password")
    app.state.auth.create_user("bob", "bob-password")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sign_in(client: TestClient) -> SignIn:
    """Log the shared client in as a fixture user (password: <name>-password)."""

    def login(username: str) -> None:
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": f"{username}-password"},
        )
        assert response.status_code == 200, response.text

    return login
