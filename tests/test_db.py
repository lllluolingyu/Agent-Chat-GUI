import sqlite3
from pathlib import Path

import pytest
from agentgui.catalog import ModelEntry

from agentchat.auth import AuthService
from agentchat.db import SCHEMA_VERSION, Database, migrate


def test_session_ownership_cascades_when_agentgui_deletes(tmp_path: Path) -> None:
    database = Database(tmp_path / "agentchat.db")
    try:
        user = AuthService(database.db).create_user("alice", "alice-password")
        rec = database.store.create(ModelEntry("m", "Model", "codex"), str(tmp_path))
        with database.db:
            database.db.execute(
                "INSERT INTO session_owners VALUES (?, ?)", (rec.id, user.id)
            )
        assert database.store.delete(rec.id)
        rows = database.db.execute("SELECT * FROM session_owners").fetchall()
        assert rows == []
    finally:
        database.close()


def test_migrations_are_idempotent_and_refuse_newer_schemas(tmp_path: Path) -> None:
    Database(tmp_path / "agentchat.db").close()
    database = Database(tmp_path / "agentchat.db")
    version = database.db.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION
    database.close()

    db = sqlite3.connect(tmp_path / "agentchat.db")
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        migrate(db)
    db.close()
