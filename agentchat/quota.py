"""Rolling-window quota rules and the usage ledger.

A turn is checked before it starts and billed as it runs. Nothing is reserved:
a turn already under way is allowed to finish even if it crosses the limit, so a
balance can go negative and the *next* turn is refused. Rolling windows mean the
refusal lifts by itself as old spend ages out.

Usage is only ever recorded from backend frames. A browser cannot supply counts,
and it cannot override a refusal.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .pricing import MICROS, PriceTable, TokenCounts, usd

# Rolling windows, in hours: ~30 days of budget and a shorter burst guard.
DEFAULT_BUDGET_WINDOW_HOURS = 720
DEFAULT_BURST_WINDOW_HOURS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def to_micros(amount: Decimal | int | float | str | None) -> int | None:
    """Convert a USD amount to whole micro-dollars (``None`` stays unlimited)."""

    if amount is None:
        return None
    value = Decimal(str(amount))
    if value < 0:
        raise ValueError("a quota limit must not be negative")
    return int((value * MICROS).to_integral_value())


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    """One user's limits. ``None`` for either amount means unlimited."""

    budget_micros: int | None = None
    budget_window_hours: int = DEFAULT_BUDGET_WINDOW_HOURS
    burst_micros: int | None = None
    burst_window_hours: int = DEFAULT_BURST_WINDOW_HOURS

    def validate(self) -> None:
        if self.budget_window_hours < 1 or self.burst_window_hours < 1:
            raise ValueError("a quota window must be at least one hour")
        for amount in (self.budget_micros, self.burst_micros):
            if amount is not None and amount < 0:
                raise ValueError("a quota limit must not be negative")

    def to_wire(self) -> dict[str, object]:
        return {
            "budget_usd": None
            if self.budget_micros is None
            else str(usd(self.budget_micros)),
            "budget_window_hours": self.budget_window_hours,
            "burst_usd": None
            if self.burst_micros is None
            else str(usd(self.burst_micros)),
            "burst_window_hours": self.burst_window_hours,
        }


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    allowed: bool
    reason: str | None = None
    # When refused: the window whose limit was hit, and when spend next ages out.
    window: str | None = None
    retry_at: datetime | None = None

    def to_wire(self) -> dict[str, object | None]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "window": self.window,
            "retry_at": None if self.retry_at is None else self.retry_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class WindowSpend:
    """Spend inside one rolling window, and the limit it is measured against."""

    name: str
    window_hours: int
    spent_micros: int
    limit_micros: int | None
    # When the oldest charge still inside this window ages out of it, which is
    # the soonest the window can free up. ``None`` when nothing is counted yet.
    resets_at: datetime | None = None

    @property
    def remaining_micros(self) -> int | None:
        if self.limit_micros is None:
            return None
        return self.limit_micros - self.spent_micros

    def to_wire(self) -> dict[str, object | None]:
        remaining = self.remaining_micros
        return {
            "window": self.name,
            "window_hours": self.window_hours,
            "spent_usd": str(usd(self.spent_micros)),
            "limit_usd": None
            if self.limit_micros is None
            else str(usd(self.limit_micros)),
            "remaining_usd": None if remaining is None else str(usd(remaining)),
            "resets_at": None if self.resets_at is None else self.resets_at.isoformat(),
        }


class QuotaService:
    def __init__(
        self, db: sqlite3.Connection, prices: PriceTable, default: QuotaPolicy
    ) -> None:
        self.db = db
        self.prices = prices
        self.default = default

    # --- policies -----------------------------------------------------------

    def policy(self, user_id: str) -> QuotaPolicy:
        row = self.db.execute(
            "SELECT * FROM quota_policies WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return self.default
        return QuotaPolicy(
            row["budget_micros"],
            row["budget_window_hours"],
            row["burst_micros"],
            row["burst_window_hours"],
        )

    def set_policy(self, user_id: str, policy: QuotaPolicy) -> QuotaPolicy:
        policy.validate()
        with self.db:
            self.db.execute(
                """INSERT INTO quota_policies VALUES (?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     budget_micros=excluded.budget_micros,
                     budget_window_hours=excluded.budget_window_hours,
                     burst_micros=excluded.burst_micros,
                     burst_window_hours=excluded.burst_window_hours,
                     updated_at=excluded.updated_at""",
                (
                    user_id,
                    policy.budget_micros,
                    policy.budget_window_hours,
                    policy.burst_micros,
                    policy.burst_window_hours,
                    _now().isoformat(),
                ),
            )
        return policy

    # --- spend --------------------------------------------------------------

    def spent_micros(self, user_id: str, window_hours: int) -> int:
        since = (_now() - timedelta(hours=window_hours)).isoformat()
        row = self.db.execute(
            """SELECT coalesce(sum(cost_micros), 0) FROM usage_ledger
               WHERE user_id = ? AND created_at > ?""",
            (user_id, since),
        ).fetchone()
        return int(row[0])

    def _resets_at(self, user_id: str, window_hours: int) -> datetime | None:
        """When the oldest charge inside this window ages out of it."""

        since = (_now() - timedelta(hours=window_hours)).isoformat()
        row = self.db.execute(
            """SELECT min(created_at) FROM usage_ledger
               WHERE user_id = ? AND created_at > ? AND cost_micros > 0""",
            (user_id, since),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        try:
            oldest = datetime.fromisoformat(row[0])
        except ValueError:
            return None
        return oldest + timedelta(hours=window_hours)

    def _window(
        self, user_id: str, name: str, hours: int, limit: int | None
    ) -> WindowSpend:
        return WindowSpend(
            name,
            hours,
            self.spent_micros(user_id, hours),
            limit,
            self._resets_at(user_id, hours),
        )

    def windows(self, user_id: str) -> list[WindowSpend]:
        policy = self.policy(user_id)
        return [
            self._window(
                user_id, "budget", policy.budget_window_hours, policy.budget_micros
            ),
            self._window(
                user_id, "burst", policy.burst_window_hours, policy.burst_micros
            ),
        ]

    def check(self, user_id: str) -> QuotaDecision:
        """Decide whether ``user_id`` may start another turn.

        Fails closed: an unreadable ledger or policy refuses the turn rather
        than letting it run unmetered.
        """
        try:
            windows = self.windows(user_id)
        except sqlite3.Error as exc:
            return QuotaDecision(False, f"quota check failed: {exc}")
        for window in windows:
            remaining = window.remaining_micros
            if remaining is not None and remaining <= 0:
                spent, limit = usd(window.spent_micros), usd(window.limit_micros or 0)
                return QuotaDecision(
                    False,
                    f"{window.name} quota exhausted: ${spent} used of ${limit} "
                    f"in the last {window.window_hours}h",
                    window.name,
                    window.resets_at,
                )
        return QuotaDecision(True)

    # --- ledger -------------------------------------------------------------

    def record(
        self,
        user_id: str,
        session_id: str | None,
        backend: str,
        model: str,
        counts: TokenCounts,
    ) -> int:
        """Bill ``counts`` to ``user_id`` and return the charge in micros."""

        micros, source = self.prices.cost_micros(backend, model, counts)
        with self.db:
            self.db.execute(
                """INSERT INTO usage_ledger (user_id, session_id, backend, model,
                     input_tokens, output_tokens, cached_tokens,
                     cache_write_tokens, reasoning_tokens, cost_micros, pricing,
                     note, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id,
                    session_id,
                    backend,
                    model,
                    counts.input,
                    counts.output,
                    counts.cached,
                    counts.cache_write,
                    counts.reasoning,
                    micros,
                    source,
                    None,
                    _now().isoformat(),
                ),
            )
        return micros

    def adjust(self, user_id: str, amount_usd: Decimal | str, note: str) -> int:
        """Record an admin credit (negative cost) or manual charge."""

        micros = int((Decimal(str(amount_usd)) * MICROS).to_integral_value())
        with self.db:
            self.db.execute(
                """INSERT INTO usage_ledger (user_id, session_id, backend, model,
                     cost_micros, pricing, note, created_at)
                   VALUES (?,NULL,'adjustment','adjustment',?,'adjustment',?,?)""",
                (user_id, micros, note[:200], _now().isoformat()),
            )
        return micros
