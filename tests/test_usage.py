from decimal import Decimal

import pytest
from agentgui.catalog import ModelEntry

from agentchat.auth import AuthService
from agentchat.db import Database
from agentchat.pricing import PriceTable, TokenCounts, usd
from agentchat.quota import QuotaPolicy, QuotaService
from agentchat.usage import UsageRecorder, parse_models

PRICES = """
[fallback]
input = 1.0
output = 1.0

[models."m"]
input = 1.0
output = 1.0
"""


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    prices = PriceTable.parse(PRICES)
    quota = QuotaService(database.db, prices, QuotaPolicy())
    user = AuthService(database.db).create_user("alice", "alice-password")
    workspace = tmp_path / "w"
    workspace.mkdir()
    session = database.store.create(ModelEntry("m", "M", "codex", "m"), str(workspace))
    try:
        yield database, quota, UsageRecorder(database.db, quota), user, session
    finally:
        database.close()


def _frame(input_tokens: int, output: int, cumulative: bool) -> dict:
    return {
        "type": "usage",
        "cumulative": cumulative,
        "models": [{"model": "m", "input": input_tokens, "output": output}],
    }


def test_cumulative_reports_are_billed_on_the_increase_only(setup) -> None:
    _, quota, recorder, user, session = setup
    first = recorder.record_frame(
        user.id, session.id, "codex", _frame(1_000_000, 0, True)
    )
    assert [c.cost_micros for c in first] == [1_000_000]
    # A repeated, unchanged total charges nothing (Codex repeats notifications).
    assert (
        recorder.record_frame(user.id, session.id, "codex", _frame(1_000_000, 0, True))
        == []
    )
    # Only the delta of a grown total is billed.
    second = recorder.record_frame(
        user.id, session.id, "codex", _frame(1_500_000, 0, True)
    )
    assert [c.counts.input for c in second] == [500_000]
    assert usd(quota.spent_micros(user.id, 720)) == Decimal("1.500000")


def test_counter_reset_bills_the_new_values_as_fresh_spend(setup) -> None:
    _, quota, recorder, user, session = setup
    recorder.record_frame(user.id, session.id, "codex", _frame(1_000_000, 0, True))
    # The agent process restarted, so its totals begin again: real new spend.
    charges = recorder.record_frame(
        user.id, session.id, "codex", _frame(200_000, 0, True)
    )
    assert [c.counts.input for c in charges] == [200_000]
    assert usd(quota.spent_micros(user.id, 720)) == Decimal("1.200000")


def test_request_scoped_reports_are_billed_as_given(setup) -> None:
    _, quota, recorder, user, session = setup
    for _ in range(3):
        recorder.record_frame(
            user.id, session.id, "lingcore", _frame(100_000, 0, False)
        )
    assert usd(quota.spent_micros(user.id, 720)) == Decimal("0.300000")
    assert recorder.snapshot(session.id, "m") is None


def test_fork_inherits_baselines_so_history_is_not_rebilled(setup) -> None:
    database, quota, recorder, user, session = setup
    recorder.record_frame(user.id, session.id, "codex", _frame(1_000_000, 0, True))
    child = database.store.clone(session, "native-child")
    recorder.copy_snapshots(session.id, child.id)
    # The fork resumes the same native agent session and re-reports its total.
    assert (
        recorder.record_frame(user.id, child.id, "codex", _frame(1_000_000, 0, True))
        == []
    )
    assert usd(quota.spent_micros(user.id, 720)) == Decimal("1.000000")


def test_malformed_usage_frames_are_ignored(setup) -> None:
    _, quota, recorder, user, session = setup
    assert parse_models({"models": "nope"}) == []
    assert parse_models({"models": [{"input": 5}, {"model": ""}]}) == []
    frame = {
        "type": "usage",
        "cumulative": False,
        "models": [{"model": "m", "input": -5, "output": True, "cached": "x"}],
    }
    assert recorder.record_frame(user.id, session.id, "lingcore", frame) == []
    assert quota.spent_micros(user.id, 720) == 0


def test_snapshots_are_removed_with_their_session(setup) -> None:
    database, _, recorder, user, session = setup
    recorder.record_frame(user.id, session.id, "codex", _frame(10, 0, True))
    assert recorder.snapshot(session.id, "m") == TokenCounts(input=10)
    database.store.delete(session.id)
    assert recorder.snapshot(session.id, "m") is None
    # Billing history survives the chat it came from.
    rows = database.db.execute("SELECT count(*) FROM usage_ledger").fetchone()[0]
    assert rows == 1
