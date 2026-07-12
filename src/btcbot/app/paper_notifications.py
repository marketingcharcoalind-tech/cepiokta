"""Paper P&L and actionable-error event policy for Telegram T.1 addendum.

The policy is transport-neutral: it emits ``BotEvent`` objects to the existing
best-effort notifier contract. It never performs network I/O and never contains
credentials. Money uses Decimal and timestamps come from an injected UTC clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from btcbot.adapters.clock import Clock
from btcbot.adapters.telegram import BotEvent, Severity
from btcbot.domain.models import RoundResult

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class EventSink(Protocol):
    async def emit(self, event: BotEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class NotificationPolicy:
    """Safe defaults matching docs/12 addendum; configurable by composition."""

    notify_wins: bool = True
    notify_losses: bool = True
    notify_equity_high: bool = True
    notify_profit_milestone: bool = True
    profit_milestone_step: Decimal = Decimal("50")
    alert_consecutive_losses: int = 3
    alert_drawdown_pct: Decimal = Decimal("5")
    error_dedup_window: timedelta = timedelta(seconds=300)

    def __post_init__(self) -> None:
        if self.profit_milestone_step <= _ZERO:
            raise ValueError("profit milestone step must be positive")
        if self.alert_consecutive_losses <= 0:
            raise ValueError("consecutive-loss threshold must be positive")
        if self.alert_drawdown_pct < _ZERO:
            raise ValueError("drawdown threshold must be non-negative")
        if self.error_dedup_window < timedelta(0):
            raise ValueError("error dedup window must be non-negative")


class PaperNotificationTracker:
    """Track session P&L state and emit one-shot operator events."""

    def __init__(
        self,
        *,
        starting_balance: Decimal,
        sink: EventSink,
        clock: Clock,
        policy: NotificationPolicy | None = None,
    ) -> None:
        if starting_balance <= _ZERO:
            raise ValueError("starting balance must be positive")
        self._starting = starting_balance
        self._peak = starting_balance
        self._sink = sink
        self._clock = clock
        self._policy = policy or NotificationPolicy()
        self._consecutive_losses = 0
        self._last_milestone = _ZERO
        self._last_drawdown_bucket = _ZERO
        self._last_errors: dict[str, datetime] = {}

    async def on_result(self, result: RoundResult) -> None:
        """Emit trade result plus threshold events after Gamma settlement."""
        ts = self._now()
        if result.pnl < _ZERO:
            self._consecutive_losses += 1
            if self._policy.notify_losses:
                await self._emit(
                    "trade_loss",
                    f"❌ #{result.round_no} {result.side_taken} PnL {result.pnl:+} | "
                    f"balance ${result.balance_after}",
                    Severity.WARN,
                    ts,
                )
        else:
            self._consecutive_losses = 0
            if self._policy.notify_wins:
                await self._emit(
                    "trade_win",
                    f"✅ #{result.round_no} {result.side_taken} PnL {result.pnl:+} | "
                    f"balance ${result.balance_after}",
                    Severity.INFO,
                    ts,
                )

        if result.balance_after > self._peak:
            self._peak = result.balance_after
            self._last_drawdown_bucket = _ZERO
            if self._policy.notify_equity_high:
                await self._emit(
                    "equity_high",
                    f"🏆 Equity high baru: ${result.balance_after}",
                    Severity.INFO,
                    ts,
                )

        await self._maybe_milestone(result.balance_after, ts)
        await self._maybe_consecutive_loss(ts)
        await self._maybe_drawdown(result.balance_after, ts)

    async def error(
        self,
        *,
        kind: str,
        detail: str,
        remediation: str,
        severity: Severity = Severity.CRITICAL,
        action_required: bool = True,
    ) -> bool:
        """Emit an actionable deduplicated error; return whether it was emitted."""
        now = self._now()
        key = f"{kind}:{detail}"
        previous = self._last_errors.get(key)
        if previous is not None and now - previous < self._policy.error_dedup_window:
            return False
        self._last_errors[key] = now
        prefix = "🔴 ACTION REQUIRED" if action_required else "⚠️ ERROR"
        await self._emit(
            "error_action_required" if action_required else "error",
            f"{prefix} — {kind}\n{detail}\n👉 Perbaiki: {remediation}",
            severity,
            now,
        )
        return True

    async def _maybe_milestone(self, balance: Decimal, ts: datetime) -> None:
        profit = balance - self._starting
        step = self._policy.profit_milestone_step
        milestone = (profit // step) * step if profit > _ZERO else _ZERO
        if (
            self._policy.notify_profit_milestone
            and milestone > self._last_milestone
            and milestone > _ZERO
        ):
            self._last_milestone = milestone
            await self._emit(
                "profit_milestone",
                f"🎯 Milestone profit sesi tembus +${milestone} | balance ${balance}",
                Severity.INFO,
                ts,
            )

    async def _maybe_consecutive_loss(self, ts: datetime) -> None:
        threshold = self._policy.alert_consecutive_losses
        if self._consecutive_losses == threshold:
            await self._emit(
                "consec_loss",
                f"🔻 ALERT: {threshold} kalah beruntun. Entry harus ditinjau/pause.",
                Severity.CRITICAL,
                ts,
            )

    async def _maybe_drawdown(self, balance: Decimal, ts: datetime) -> None:
        if self._peak <= _ZERO:
            return
        drawdown = max(_ZERO, (self._peak - balance) * _HUNDRED / self._peak)
        threshold = self._policy.alert_drawdown_pct
        if threshold == _ZERO or drawdown < threshold:
            return
        bucket = (drawdown // threshold) * threshold
        if bucket <= self._last_drawdown_bucket:
            return
        self._last_drawdown_bucket = bucket
        await self._emit(
            "drawdown",
            f"🔻 Drawdown {drawdown:.2f}% | balance ${balance} | peak ${self._peak}",
            Severity.CRITICAL,
            ts,
        )

    async def _emit(self, kind: str, text: str, severity: Severity, ts: datetime) -> None:
        await self._sink.emit(BotEvent(kind=kind, text=text, severity=severity, ts=ts))

    def _now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("notification clock must be timezone-aware")
        return now
