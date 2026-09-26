"""The chat turn boundary: ownership, quota, and billing around one socket.

``agentgui.server.ChatConnection`` already owns the per-connection rules (one
reader, one active turn, one approval registry). This subclass adds the product
rules: a turn is refused before it starts when the owner is out of quota, and
every ``usage`` frame the backend emits is billed to that owner as it streams.

Ownership itself is enforced before a connection is built — see
``agentchat.app`` — so a backend process is never started for a session the
authenticated user does not own.
"""

from __future__ import annotations

from typing import Any

from agentgui.protocol import Frame, frame
from agentgui.server import ChatConnection
from agentgui.store import SessionRecord, Store
from fastapi import WebSocket

from .domain import User
from .pricing import usd
from .quota import QuotaService
from .usage import UsageRecorder


class UserChatConnection(ChatConnection):
    def __init__(
        self,
        ws: WebSocket,
        rec: SessionRecord,
        store: Store,
        backend: Any,
        *,
        user: User,
        quota: QuotaService,
        recorder: UsageRecorder,
    ) -> None:
        super().__init__(ws, rec, store, backend)
        self.user = user
        self.quota = quota
        self.recorder = recorder

    async def send(
        self,
        value: Frame | Any,
        *,
        transcript: bool = False,
    ) -> None:
        """Bill a usage frame before it reaches the browser.

        Billing happens here rather than at turn end so a disconnect, a stop, or
        a crashed backend still charges what the agent already spent.
        """
        wire = value if isinstance(value, dict) else value.to_wire()
        if wire.get("type") == "usage":
            wire = self._bill(wire)
        await super().send(wire, transcript=transcript)

    def _bill(self, wire: dict[str, Any]) -> dict[str, Any]:
        try:
            charges = self.recorder.record_frame(
                self.user.id, self.session.id, self.session.backend, wire
            )
        except Exception as exc:  # a billing failure must not kill the turn
            return {**wire, "billing_error": str(exc)}
        charged = sum(charge.cost_micros for charge in charges)
        # Replace the agent's own cost estimate with what this product charged,
        # and report the owner's remaining budget alongside it.
        return {
            **wire,
            "cost_usd": float(usd(charged)),
            "charged_usd": str(usd(charged)),
            "quota": [window.to_wire() for window in self.quota.windows(self.user.id)],
        }

    async def input(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind in {"user", "edit"} and not self.busy:
            decision = self.quota.check(self.user.id)
            if not decision.allowed:
                await self.send(
                    frame(
                        "quota_exceeded",
                        message=decision.reason,
                        window=decision.window,
                        retry_at=decision.retry_at.isoformat()
                        if decision.retry_at
                        else None,
                    )
                )
                # A refused submission ends the turn slot exactly like a
                # rejected one, so the composer re-enables in the browser.
                await self.send(
                    frame("turn_end")
                    if kind == "user"
                    else frame("edit_rejected", message=decision.reason)
                )
                return
        await super().input(msg)
