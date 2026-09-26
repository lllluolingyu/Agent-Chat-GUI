"""Product tables stored beside agentgui's session store.

agentgui's ``Store`` owns ``sessions`` and ``turns``. This module adds users,
login sessions, and session ownership to the same SQLite file so ownership rows
cascade when agentgui deletes a chat session. Migrations are applied in order
and tracked with ``PRAGMA user_version``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agentgui.store import Store

# Each entry upgrades the schema by one version. Never edit a released entry;
# append a new one instead.
_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE auth_sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX auth_sessions_user ON auth_sessions(user_id);
    CREATE TABLE session_owners (
        session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE INDEX session_owners_user ON session_owners(user_id);
    """,
    # Money is stored in micro-dollars (1e-6 USD) so SQLite sums stay exact.
    """
    CREATE TABLE quota_policies (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        budget_micros INTEGER,
        budget_window_hours INTEGER NOT NULL,
        burst_micros INTEGER,
        burst_window_hours INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    );
    -- Billing history outlives a deleted chat, so session_id carries no
    -- foreign key. An adjustment row (admin top-up) has a negative cost.
    CREATE TABLE usage_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        session_id TEXT,
        backend TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cached_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost_micros INTEGER NOT NULL,
        pricing TEXT NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX usage_ledger_user_time ON usage_ledger(user_id, created_at);
    -- Last cumulative counters seen per (session, model); a backend that
    -- reports running totals is billed on the difference from these.
    CREATE TABLE usage_snapshots (
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cached_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (session_id, model)
    );
    """,
)

SCHEMA_VERSION = len(_MIGRATIONS)


def migrate(db: sqlite3.Connection) -> None:
    current = db.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema {current} is newer than this build ({SCHEMA_VERSION})"
        )
    for version in range(current, SCHEMA_VERSION):
        with db:
            db.executescript(
                f"BEGIN; {_MIGRATIONS[version]} PRAGMA user_version = {version + 1};"
            )


class Database:
    """One SQLite file shared by the agentgui store and the product tables."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The store creates ``sessions`` first; ``session_owners`` references it.
        self.store = Store(self.path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        migrate(self.db)

    def close(self) -> None:
        try:
            self.db.close()
        finally:
            self.store.close()
