"""Turn backend ``usage`` frames into ledger charges.

LingChat reports what each agent said and marks whether the counters are
session running totals (Claude, Codex) or cover one request (LingCore). This
module owns the stateful part: for a cumulative backend it keeps the last
counters per (session, model) and bills only the increase, so a reconnect, a
resumed session, or a repeated notification cannot double-charge.

A counter that moves *backwards* means the agent process restarted and began
counting again, so the new values are fresh spend and are billed as they stand.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .pricing import TokenCounts
from .quota import QuotaService

_FIELDS = ("input", "output", "cached", "cache_write", "reasoning")
_COLUMNS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cached": "cached_tokens",
    "cache_write": "cache_write_tokens",
    "reasoning": "reasoning_tokens",
}


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


def parse_models(frame: dict[str, Any]) -> list[tuple[str, TokenCounts]]:
    """Read ``(model, counts)`` pairs out of a ``usage`` frame."""

    entries = frame.get("models")
    if not isinstance(entries, list):
        return []
    parsed: list[tuple[str, TokenCounts]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model = entry.get("model")
        if not isinstance(model, str) or not model:
            continue
        parsed.append(
            (model, TokenCounts(**{field: _int(entry.get(field)) for field in _FIELDS}))
        )
    return parsed


@dataclass(frozen=True, slots=True)
class Charge:
    model: str
    counts: TokenCounts
    cost_micros: int


class UsageRecorder:
    def __init__(self, db: sqlite3.Connection, quota: QuotaService) -> None:
        self.db = db
        self.quota = quota

    def snapshot(self, session_id: str, model: str) -> TokenCounts | None:
        row = self.db.execute(
            "SELECT * FROM usage_snapshots WHERE session_id = ? AND model = ?",
            (session_id, model),
        ).fetchone()
        if row is None:
            return None
        return TokenCounts(**{field: row[_COLUMNS[field]] for field in _FIELDS})

    def _save_snapshot(self, session_id: str, model: str, counts: TokenCounts) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO usage_snapshots
                     (session_id, model, input_tokens, output_tokens, cached_tokens,
                      cache_write_tokens, reasoning_tokens)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(session_id, model) DO UPDATE SET
                     input_tokens=excluded.input_tokens,
                     output_tokens=excluded.output_tokens,
                     cached_tokens=excluded.cached_tokens,
                     cache_write_tokens=excluded.cache_write_tokens,
                     reasoning_tokens=excluded.reasoning_tokens""",
                (
                    session_id,
                    model,
                    counts.input,
                    counts.output,
                    counts.cached,
                    counts.cache_write,
                    counts.reasoning,
                ),
            )

    def copy_snapshots(self, parent_id: str, child_id: str) -> None:
        """Carry baselines into a fork.

        A forked chat inherits the parent's native agent session, so its first
        cumulative report still includes spend already billed to the parent.
        """
        with self.db:
            self.db.execute(
                """INSERT OR REPLACE INTO usage_snapshots
                   SELECT ?, model, input_tokens, output_tokens, cached_tokens,
                          cache_write_tokens, reasoning_tokens
                   FROM usage_snapshots WHERE session_id = ?""",
                (child_id, parent_id),
            )

    def billable(
        self, session_id: str, model: str, reported: TokenCounts, *, cumulative: bool
    ) -> TokenCounts:
        """Return the counts to charge, updating the baseline when cumulative."""

        if not cumulative:
            return reported
        previous = self.snapshot(session_id, model)
        self._save_snapshot(session_id, model, reported)
        if previous is None:
            return reported
        values = {
            field: getattr(reported, field) - getattr(previous, field)
            for field in _FIELDS
        }
        if any(delta < 0 for delta in values.values()):
            # The agent restarted its counters; everything reported is new spend.
            return reported
        return TokenCounts(**values)

    def record_frame(
        self, user_id: str, session_id: str, backend: str, frame: dict[str, Any]
    ) -> list[Charge]:
        """Bill one ``usage`` frame and return what was charged."""

        cumulative = bool(frame.get("cumulative"))
        charges: list[Charge] = []
        for model, reported in parse_models(frame):
            counts = self.billable(session_id, model, reported, cumulative=cumulative)
            if not counts:
                continue
            micros = self.quota.record(user_id, session_id, backend, model, counts)
            charges.append(Charge(model, counts, micros))
        return charges
