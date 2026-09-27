"""Settings and the shared service context the route modules are built from."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from agentgui.backends.lingcore_backend import ProfileCache
from agentgui.catalog import Catalog
from agentgui.server import BackendFactory, make_backend

from .auth import AuthService
from .chat import UserChatConnection
from .db import Database
from .pricing import PriceTable
from .quota import (
    DEFAULT_BUDGET_WINDOW_HOURS,
    DEFAULT_BURST_WINDOW_HOURS,
    QuotaPolicy,
    QuotaService,
    to_micros,
)
from .usage import UsageRecorder
from .workspaces import Workspaces

SESSION_COOKIE = "agentchat_session"


def default_data_dir() -> Path:
    return (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "agent-chat"
    )


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    # Default limits for a user without an explicit policy.
    default_budget_usd: Decimal | None = Decimal("20")
    default_burst_usd: Decimal | None = Decimal("3")
    catalog_path: Path | None = None
    workspace_root: Path | None = None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "agentchat.db"

    @property
    def prices_path(self) -> Path:
        return self.data_dir / "prices.toml"

    @property
    def workspaces_path(self) -> Path:
        return self.workspace_root or self.data_dir / "workspaces"

    def default_policy(self) -> QuotaPolicy:
        policy = QuotaPolicy(
            to_micros(self.default_budget_usd),
            DEFAULT_BUDGET_WINDOW_HOURS,
            to_micros(self.default_burst_usd),
            DEFAULT_BURST_WINDOW_HOURS,
        )
        policy.validate()
        return policy


@dataclass
class Context:
    """Long-lived services shared by every route and connection."""

    settings: Settings
    database: Database
    auth: AuthService
    quota: QuotaService
    recorder: UsageRecorder
    workspaces: Workspaces
    catalog: Catalog
    prices: PriceTable
    profiles: ProfileCache
    backend_factory: BackendFactory = make_backend
    # One live connection per session id, exactly as agentgui does per process.
    attached: dict[str, UserChatConnection | None] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        catalog: Catalog | None = None,
        backend_factory: BackendFactory = make_backend,
    ) -> Context:
        database = Database(settings.database_path)
        prices = PriceTable.load(settings.prices_path)
        quota = QuotaService(database.db, prices, settings.default_policy())
        return cls(
            settings=settings,
            database=database,
            auth=AuthService(database.db),
            quota=quota,
            recorder=UsageRecorder(database.db, quota),
            workspaces=Workspaces(settings.workspaces_path),
            catalog=catalog or Catalog.load(settings.catalog_path),
            prices=prices,
            profiles=ProfileCache(),
            backend_factory=backend_factory,
        )

    @property
    def store(self):  # type: ignore[no-untyped-def]
        return self.database.store

    async def close(self) -> None:
        for connection in list(self.attached.values()):
            if connection:
                await connection.close()
        self.profiles.close()
        self.database.close()
