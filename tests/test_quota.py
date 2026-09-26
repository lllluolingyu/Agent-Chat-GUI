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


def test_only_a_dated_snapshot_inherits_a_listed_rate() -> None:
    """A new sibling must be priced deliberately, not inherit a cheap relative."""

    table = PriceTable.parse(PRICES)
    for snapshot in ("priced-model-20260901", "priced-model-2026-09-01"):
        _, source = table.cost_micros("codex", snapshot, TokenCounts(input=1))
        assert source == "listed", snapshot
    # "priced-model-turbo" starts with a listed id but is a different model.
    for other in ("priced-model-turbo", "priced-model-2", "priced-model-20269"):
        _, source = table.cost_micros("codex", other, TokenCounts(input=1))
        assert source == "fallback", other


def test_shipped_table_prices_every_model_it_lists() -> None:
    """The file the product installs must parse and cover both providers."""

    from agentchat.pricing import DEFAULT_PRICES_FILE

    table = PriceTable.parse(DEFAULT_PRICES_FILE.read_text(encoding="utf-8"))
    assert len(table.models) > 30
    for model, price in table.models.items():
        assert price.input > 0 and price.output > 0, model
        # A cached or cache-written token is never free: an unpublished rate is
        # listed at the model's full input rate instead.
        assert price.cached_input > 0 and price.cache_write > 0, model
    # Anthropic counts cache reads beside input; OpenAI counts them inside it.
    claude = table.cost_micros(
        "claude", "claude-sonnet-5", TokenCounts(input=1_000_000, cached=1_000_000)
    )
    codex = table.cost_micros(
        "codex", "gpt-6-astra", TokenCounts(input=1_000_000, cached=1_000_000)
    )
    assert usd(claude[0]) == Decimal("2.200000")  # 1M at $2 + 1M cached at $0.20
    assert usd(codex[0]) == Decimal("1.000000")  # all 1M was cached, at $1


def test_window_reports_when_its_oldest_spend_ages_out(tmp_path, prices) -> None:
    from agentchat.auth import AuthService
    from agentchat.db import Database

    database = Database(tmp_path / "quota.db")
    try:
        user = AuthService(database.db).create_user("alice", "password123")
        quota = _service(database, prices)
        # Nothing counted yet, so there is nothing to age out.
        assert all(w.resets_at is None for w in quota.windows(user.id))
        quota.record(user.id, None, "codex", "priced-model", TokenCounts(output=10))
        for window in quota.windows(user.id):
            assert window.resets_at is not None
            hours = (
                window.resets_at - datetime.now(timezone.utc)
            ).total_seconds() / 3600
            assert hours == pytest.approx(window.window_hours, abs=0.1)
        # A refusal carries the same instant, so the browser can show it.
        quota.record(
            user.id, None, "codex", "priced-model", TokenCounts(output=5_000_000)
        )
        decision = quota.check(user.id)
        assert not decision.allowed and decision.retry_at is not None
    finally:
        database.close()
