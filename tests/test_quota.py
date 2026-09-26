from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agentchat.pricing import PriceTable, TokenCounts, usd
from agentchat.quota import QuotaPolicy, QuotaService, to_micros

PRICES = """
[fallback]
input = 10.0
output = 30.0

[models."priced-model"]
input = 1.0
cached_input = 0.1
cache_write = 1.25
output = 2.0
"""


@pytest.fixture
def prices() -> PriceTable:
    return PriceTable.parse(PRICES)


def test_openai_convention_treats_cached_as_part_of_input(prices: PriceTable) -> None:
    # 1M input of which 400k cached: 600k * $1 + 400k * $0.1 = $0.64, plus
    # 100k output at $2 = $0.20.
    micros, source = prices.cost_micros(
        "codex",
        "priced-model",
        TokenCounts(input=1_000_000, cached=400_000, output=100_000),
    )
    assert source == "listed"
    assert usd(micros) == Decimal("0.840000")


def test_anthropic_convention_bills_cache_reads_beside_input(
    prices: PriceTable,
) -> None:
    # Claude reports cache reads and writes separately, so input is not reduced.
    micros, _ = prices.cost_micros(
        "claude",
        "priced-model",
        TokenCounts(input=1_000_000, cached=400_000, cache_write=200_000, output=0),
    )
    assert usd(micros) == Decimal("1.290000")


def test_unlisted_model_uses_fallback_and_dated_snapshot_matches_prefix(
    prices: PriceTable,
) -> None:
    micros, source = prices.cost_micros(
        "codex", "mystery", TokenCounts(input=1_000_000)
    )
    assert (usd(micros), source) == (Decimal("10.000000"), "fallback")
    micros, source = prices.cost_micros(
        "codex", "priced-model-20260901", TokenCounts(input=1_000_000)
    )
    assert (usd(micros), source) == (Decimal("1.000000"), "listed")


def _service(database, prices: PriceTable, **kw) -> QuotaService:
    policy = QuotaPolicy(to_micros("1.00"), 720, to_micros("0.50"), 5, **kw)
    return QuotaService(database.db, prices, policy)


def test_rolling_windows_refuse_and_report_when_spend_ages_out(
    tmp_path, prices
) -> None:
    from agentchat.auth import AuthService
    from agentchat.db import Database

    database = Database(tmp_path / "db.sqlite")
    try:
        user = AuthService(database.db).create_user("alice", "alice-password")
        quota = _service(database, prices)
        assert quota.check(user.id).allowed

        # $0.60 of output: under the $1 budget but over the $0.50 burst limit.
        quota.record(
            user.id, None, "codex", "priced-model", TokenCounts(output=300_000)
        )
        decision = quota.check(user.id)
        assert not decision.allowed and decision.window == "burst"
        assert "burst quota exhausted" in (decision.reason or "")
        # The refusal lifts once that charge leaves the 5h window.
        assert decision.retry_at is not None
        expected = datetime.now(timezone.utc) + timedelta(hours=5)
        assert abs((decision.retry_at - expected).total_seconds()) < 120

        # An admin credit ages out with the window but restores access now.
        quota.adjust(user.id, Decimal("-0.40"), "top-up")
        assert quota.check(user.id).allowed
        windows = {w.name: w for w in quota.windows(user.id)}
        assert usd(windows["burst"].spent_micros) == Decimal("0.200000")
    finally:
        database.close()


def test_overrun_goes_negative_rather_than_stopping_a_running_turn(
    tmp_path, prices
) -> None:
    from agentchat.auth import AuthService
    from agentchat.db import Database

    database = Database(tmp_path / "db.sqlite")
    try:
        user = AuthService(database.db).create_user("alice", "alice-password")
        quota = _service(database, prices)
        # One turn may spend far past the limit; only the next one is refused.
        quota.record(
            user.id, None, "codex", "priced-model", TokenCounts(output=5_000_000)
        )
        windows = {w.name: w for w in quota.windows(user.id)}
        assert windows["budget"].remaining_micros is not None
        assert windows["budget"].remaining_micros < 0
        assert not quota.check(user.id).allowed
    finally:
        database.close()


def test_policy_round_trip_and_validation(tmp_path, prices) -> None:
    from agentchat.auth import AuthService
    from agentchat.db import Database

    database = Database(tmp_path / "db.sqlite")
    try:
        user = AuthService(database.db).create_user("alice", "alice-password")
        quota = _service(database, prices)
        quota.set_policy(user.id, QuotaPolicy(None, 24, to_micros("2.5"), 1))
        stored = quota.policy(user.id)
        assert stored.budget_micros is None and stored.burst_micros == 2_500_000
        assert stored.to_wire()["burst_usd"] == "2.500000"
        # An unlimited budget never refuses.
        assert quota.check(user.id).allowed
        with pytest.raises(ValueError, match="at least one hour"):
            quota.set_policy(user.id, QuotaPolicy(None, 0, None, 5))
        with pytest.raises(ValueError, match="negative"):
            to_micros(Decimal("-1"))
    finally:
        database.close()
