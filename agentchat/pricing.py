"""Token prices, owned by this product rather than any agent.

Every backend is billed from token counts through one operator-maintained table,
so dollars mean the same thing across Claude, Codex, and LingCore. An agent's
own cost estimate is display-only (the Claude SDK documents ``total_cost_usd``
as a client-side estimate that must not bill end users).

Backends differ in what their counters include, so each one declares whether
``cached`` sits inside ``input``: Anthropic reports cache reads and writes
beside input, while OpenAI-shaped counters (Codex, LingCore) report cached
input inside it. ``reasoning`` is always part of ``output``.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

MICROS = Decimal("1000000")
PER_MTOK = Decimal("1000000")

# True when the reported ``cached`` count is already part of ``input``.
CACHED_INSIDE_INPUT = {"claude": False, "codex": True, "lingcore": True}

# The seed table, copied to the data directory on first run and edited there.
DEFAULT_PRICES_FILE = Path(__file__).with_name("prices.default.toml")

# A provider may serve a dated snapshot of a listed alias (claude-opus-5-5-20260901,
# gpt-4.1-2025-04-14, or Vertex's claude-opus-5@20260901). Only that suffix makes
# an unlisted id resolve to a listed price: a new sibling of a listed model must
# be priced deliberately rather than inherit a cheaper relative's rate.
_SNAPSHOT_SUFFIX = re.compile(r"^[-@](\d{8}|\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per million tokens for one model."""

    input: Decimal
    output: Decimal
    cached_input: Decimal = Decimal("0")
    cache_write: Decimal = Decimal("0")

    @classmethod
    def parse(cls, raw: Any, label: str) -> ModelPrice:
        if not isinstance(raw, dict):
            raise ValueError(f"{label}: price must be a table")
        values: dict[str, Decimal] = {}
        for key in ("input", "output", "cached_input", "cache_write"):
            if key not in raw:
                continue
            try:
                value = Decimal(str(raw[key]))
            except Exception:
                raise ValueError(f"{label}.{key}: price must be a number") from None
            if value < 0:
                raise ValueError(f"{label}.{key}: price must not be negative")
            values[key] = value
        for required in ("input", "output"):
            if required not in values:
                raise ValueError(f"{label}: {required} price is required")
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class TokenCounts:
    """Tokens billed for one charge, in a backend's own convention."""

    input: int = 0
    output: int = 0
    cached: int = 0
    cache_write: int = 0
    reasoning: int = 0

    def __bool__(self) -> bool:
        return any(
            (self.input, self.output, self.cached, self.cache_write, self.reasoning)
        )


class PriceTable:
    def __init__(self, fallback: ModelPrice, models: dict[str, ModelPrice]) -> None:
        self.fallback = fallback
        self.models = models

    @classmethod
    def load(cls, path: str | Path) -> PriceTable:
        target = Path(path)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                DEFAULT_PRICES_FILE.read_text(encoding="utf-8"), encoding="utf-8"
            )
        return cls.parse(target.read_text(encoding="utf-8"))

    @classmethod
    def parse(cls, text: str) -> PriceTable:
        data = tomllib.loads(text)
        raw_models = data.get("models", {})
        if not isinstance(raw_models, dict):
            raise ValueError("models must be a table of model ids")
        return cls(
            ModelPrice.parse(data.get("fallback", {}), "fallback"),
            {
                name: ModelPrice.parse(raw, f"models.{name}")
                for name, raw in raw_models.items()
            },
        )

    def price_for(self, model: str) -> tuple[ModelPrice, str]:
        """Return the price for ``model`` and whether it was listed."""

        listed = self.models.get(model)
        if listed is not None:
            return listed, "listed"
        snapshots = [
            name
            for name in self.models
            if model.startswith(name) and _SNAPSHOT_SUFFIX.match(model[len(name) :])
        ]
        if snapshots:
            return self.models[max(snapshots, key=len)], "listed"
        return self.fallback, "fallback"

    def cost_micros(
        self, backend: str, model: str, counts: TokenCounts
    ) -> tuple[int, str]:
        """Return whole micro-dollars for ``counts`` and the pricing source.

        Rounds half up at the micro-dollar, so a charge is never silently free.
        """
        price, source = self.price_for(model)
        cached = (
            min(counts.cached, counts.input)
            if CACHED_INSIDE_INPUT.get(backend, True)
            else counts.cached
        )
        uncached_input = (
            counts.input - cached
            if CACHED_INSIDE_INPUT.get(backend, True)
            else counts.input
        )
        total = (
            max(0, uncached_input) * price.input
            + max(0, cached) * price.cached_input
            + max(0, counts.cache_write) * price.cache_write
            + max(0, counts.output) * price.output
        ) / PER_MTOK
        micros = (total * MICROS).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(micros), source


def usd(micros: int) -> Decimal:
    """Convert micro-dollars to a USD amount for display."""

    return (Decimal(micros) / MICROS).quantize(Decimal("0.000001"))
