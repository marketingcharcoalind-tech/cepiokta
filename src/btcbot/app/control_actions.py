"""Confirmed Telegram pause/resume/kill actions for T.3.

All actions require whitelist authorization and a single-use, expiring second
step. Tokens are generated outside logs, consumed before execution, and cannot
change MODE or risk limits. Actions reuse the same RiskManager as paper core.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from btcbot.adapters.clock import Clock
from btcbot.risk.manager import RiskManager

_CONFIRM_TTL = timedelta(seconds=60)
_CALLBACK_PARTS = 3


class ControlAction(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    KILL = "kill"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    chat_id: int
    action: ControlAction
    phase: str
    ts: datetime
    detail: str


class AuditSink(Protocol):
    async def write(self, event: AuditEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class ActionReply:
    text: str
    confirm_callback: str | None = None
    cancel_callback: str | None = None


@dataclass(frozen=True, slots=True)
class _PendingAction:
    chat_id: int
    action: ControlAction
    expires_at: datetime


class TelegramActionController:
    """Two-step action controller with whitelist, expiry, anti-replay, and audit."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        risk: RiskManager,
        clock: Clock,
        audit: AuditSink,
        allowed_chat_ids: set[int],
        token_factory: Callable[[], str] | None = None,
        confirmation_ttl: timedelta = _CONFIRM_TTL,
    ) -> None:
        if confirmation_ttl <= timedelta(0):
            raise ValueError("confirmation_ttl must be positive")
        self._risk = risk
        self._clock = clock
        self._audit = audit
        self._allowed = frozenset(allowed_chat_ids)
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(18))
        self._ttl = confirmation_ttl
        self._pending: dict[str, _PendingAction] = {}

    async def request(self, chat_id: int, action: ControlAction) -> ActionReply | None:
        """Create confirmation callbacks but do not execute the action."""
        if chat_id not in self._allowed:
            return None
        now = self._now()
        token = self._token_factory()
        if not token or token in self._pending:
            raise RuntimeError("confirmation token collision")
        self._pending[token] = _PendingAction(chat_id, action, now + self._ttl)
        await self._audit.write(AuditEvent(chat_id, action, "requested", now, "awaiting_confirm"))
        return ActionReply(
            text=f"Confirm {action.value}? This expires in {int(self._ttl.total_seconds())}s.",
            confirm_callback=f"control:confirm:{token}",
            cancel_callback=f"control:cancel:{token}",
        )

    async def handle_callback(self, chat_id: int, callback: str) -> ActionReply | None:
        """Confirm or cancel one pending action; callbacks are single-use."""
        if chat_id not in self._allowed:
            return None
        parts = callback.split(":", 2)
        valid = (
            len(parts) == _CALLBACK_PARTS
            and parts[0] == "control"
            and parts[1] in {"confirm", "cancel"}
        )
        if not valid:
            return ActionReply("Invalid control callback.")
        operation, token = parts[1], parts[2]
        pending = self._pending.pop(token, None)
        if pending is None or pending.chat_id != chat_id:
            return ActionReply("Confirmation expired or already used.")
        now = self._now()
        if now > pending.expires_at:
            await self._audit.write(
                AuditEvent(chat_id, pending.action, "expired", now, "not_executed")
            )
            return ActionReply("Confirmation expired. Request the action again.")
        if operation == "cancel":
            await self._audit.write(
                AuditEvent(chat_id, pending.action, "cancelled", now, "not_executed")
            )
            return ActionReply(f"{pending.action.value} cancelled.")
        self._execute(pending.action, chat_id)
        await self._audit.write(AuditEvent(chat_id, pending.action, "executed", now, "confirmed"))
        return ActionReply(f"{pending.action.value} executed.")

    async def command(self, chat_id: int, text: str) -> ActionReply | None:
        """Map only /pause, /resume, and /kill to confirmation requests."""
        mapping = {
            "/pause": ControlAction.PAUSE,
            "/resume": ControlAction.RESUME,
            "/kill": ControlAction.KILL,
        }
        command = text.strip().split("@", 1)[0].lower()
        action = mapping.get(command)
        if action is None:
            return None if chat_id not in self._allowed else ActionReply("Unknown control command.")
        return await self.request(chat_id, action)

    def _execute(self, action: ControlAction, chat_id: int) -> None:
        if action is ControlAction.PAUSE:
            self._risk.pause()
        elif action is ControlAction.RESUME:
            self._risk.resume()
        else:
            self._risk.kill(f"telegram:{chat_id}")

    def _now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("control clock must be timezone-aware")
        return now
