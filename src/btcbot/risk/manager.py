"""Fail-closed risk gate for paper trading (docs/06, Prompt 2.1).

Every future paper order must pass :meth:`RiskManager.check`.  This module has no
OMS, network, signing, secret, or live-order dependency.  Money values use
``Decimal`` and the rolling rate-limit clock is injectable and UTC-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from btcbot.adapters.clock import Clock
from btcbot.domain.models import OrderRequest

if TYPE_CHECKING:
    from btcbot.config.settings import Settings

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_RATE_WINDOW = timedelta(minutes=1)


class RiskAction(StrEnum):
    """Intent classification used to distinguish increasing and reducing risk."""

    ENTRY = "entry"
    HEDGE = "hedge"
    EXIT = "exit"


class CircuitReason(StrEnum):
    """Technical or market-health conditions that block new risk."""

    WSS_DISCONNECTED = "wss_disconnected"
    WSS_RECONNECTING = "wss_reconnecting"
    PRICE_STALE = "price_stale"
    CLOCK_DRIFT = "clock_drift"
    ABNORMAL_SPREAD = "abnormal_spread"
    LOW_LIQUIDITY = "low_liquidity"
    LATENCY_BREACH = "latency_breach"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Hard limits enforced before an order reaches a paper OMS."""

    max_notional_round: Decimal
    max_open_exposure: Decimal
    max_daily_loss_pct: Decimal
    max_consec_losses: int
    min_balance: Decimal
    max_orders_per_min: int

    def __post_init__(self) -> None:
        decimal_values = (
            self.max_notional_round,
            self.max_open_exposure,
            self.max_daily_loss_pct,
            self.min_balance,
        )
        if any(value < _ZERO for value in decimal_values):
            raise ValueError("risk limits must be non-negative")
        if self.max_notional_round == _ZERO or self.max_orders_per_min <= 0:
            raise ValueError("notional and rate limits must be positive")
        if self.max_consec_losses <= 0:
            raise ValueError("max_consec_losses must be positive")

    @classmethod
    def from_settings(cls, settings: Settings) -> RiskLimits:
        """Build limits from the existing validated settings without touching .env."""
        return cls(
            max_notional_round=settings.max_notional_round,
            max_open_exposure=settings.max_open_exposure,
            max_daily_loss_pct=settings.max_daily_loss_pct,
            max_consec_losses=settings.max_consec_losses,
            min_balance=settings.bankroll_floor,
            max_orders_per_min=settings.max_orders_per_min,
        )


@dataclass(frozen=True, slots=True)
class RiskOrder:
    """Order request plus context needed by the risk gate."""

    request: OrderRequest
    round_no: int
    action: RiskAction

    @property
    def notional(self) -> Decimal:
        """Unsigned order notional in dollars."""
        return self.request.price * self.request.size

    @property
    def increases_risk(self) -> bool:
        """Entry and hedge consume caps; exit is explicitly risk-reducing."""
        return self.action in {RiskAction.ENTRY, RiskAction.HEDGE}


@dataclass(frozen=True, slots=True)
class RiskState:
    """Immutable account/ledger snapshot supplied for one risk decision."""

    balance: Decimal
    day_start_balance: Decimal
    open_exposure: Decimal
    round_notional: Decimal
    consecutive_losses: int
    recent_order_timestamps: tuple[datetime, ...] = ()


@dataclass(frozen=True, slots=True)
class Allow:
    """Positive risk decision."""

    reason: str = "allowed"


@dataclass(frozen=True, slots=True)
class Veto:
    """Negative risk decision with a stable machine-readable reason."""

    reason: str


RiskDecision = Allow | Veto


class RiskManager:
    """Stateful final gate for paper orders, kill-switches, and breakers.

    Pause and ordinary circuit breakers block only new risk, so an EXIT can still
    reduce exposure.  A manual/automatic kill or reconciliation mismatch blocks
    every action and cannot be silently cleared by ``resume``.
    """

    def __init__(self, limits: RiskLimits, clock: Clock) -> None:
        self._limits = limits
        self._clock = clock
        self._paused = False
        self._killed = False
        self._kill_reason: str | None = None
        self._breakers: set[CircuitReason] = set()

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def killed(self) -> bool:
        return self._killed

    @property
    def kill_reason(self) -> str | None:
        return self._kill_reason

    @property
    def active_breakers(self) -> frozenset[CircuitReason]:
        return frozenset(self._breakers)

    def pause(self) -> None:
        """Pause risk-increasing orders while preserving safe exits."""
        self._paused = True

    def resume(self) -> None:
        """Remove manual pause only; never clear kill or health breakers."""
        self._paused = False

    def kill(self, reason: str) -> None:
        """Latch the kill-switch.  There is intentionally no implicit reset."""
        normalized = reason.strip()
        self._killed = True
        self._kill_reason = normalized or "manual_kill"

    def on_event(self, reason: CircuitReason, *, active: bool = True) -> None:
        """Activate or clear a circuit breaker.

        Reconciliation mismatch is fatal and latches the kill-switch because the
        account state can no longer be trusted.
        """
        if active:
            self._breakers.add(reason)
            if reason is CircuitReason.RECONCILIATION_MISMATCH:
                self.kill(reason.value)
            return
        self._breakers.discard(reason)

    def should_halt(self) -> bool:
        """Return whether new entries must halt."""
        return self._killed or self._paused or bool(self._breakers)

    def check(self, order: RiskOrder, state: RiskState) -> RiskDecision:
        """Return ``Allow`` or ``Veto`` and fail closed on invalid state."""
        now = self._utc_now()
        invalid_reason = self._validate(order, state, now)
        if invalid_reason is not None:
            return Veto(invalid_reason)

        if self._killed:
            return Veto(f"kill_switch:{self._kill_reason or 'active'}")

        automatic = self._automatic_kill_reason(state)
        if automatic is not None:
            self.kill(automatic)
            return Veto(f"kill_switch:{automatic}")

        if order.increases_risk:
            if self._paused:
                return Veto("paused")
            if self._breakers:
                reasons = ",".join(sorted(reason.value for reason in self._breakers))
                return Veto(f"circuit_breaker:{reasons}")

        recent_count = sum(
            now - _RATE_WINDOW < timestamp <= now for timestamp in state.recent_order_timestamps
        )
        if recent_count >= self._limits.max_orders_per_min:
            return Veto("max_orders_per_min")

        if order.increases_risk:
            if state.round_notional + order.notional > self._limits.max_notional_round:
                return Veto("max_notional_round")
            if state.open_exposure + order.notional > self._limits.max_open_exposure:
                return Veto("max_open_exposure")

        return Allow()

    def _utc_now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("RiskManager clock must return timezone-aware time")
        return now.astimezone(UTC)

    @staticmethod
    def _validate(order: RiskOrder, state: RiskState, now: datetime) -> str | None:
        request = order.request
        if order.round_no < 0:
            return "invalid_round"
        if request.price <= _ZERO or request.size <= _ZERO:
            return "invalid_order_value"
        if request.side not in {"BUY", "SELL"}:
            return "invalid_order_side"
        if request.order_type not in {"FOK", "FAK", "GTC"}:
            return "invalid_order_type"
        if (
            state.balance < _ZERO
            or state.day_start_balance <= _ZERO
            or state.open_exposure < _ZERO
            or state.round_notional < _ZERO
            or state.consecutive_losses < 0
        ):
            return "invalid_risk_state"
        for timestamp in state.recent_order_timestamps:
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                return "invalid_order_timestamp"
            if timestamp.astimezone(UTC) > now:
                return "future_order_timestamp"
        return None

    def _automatic_kill_reason(self, state: RiskState) -> str | None:
        if state.balance < self._limits.min_balance:
            return "min_balance"
        daily_loss = max(_ZERO, state.day_start_balance - state.balance)
        daily_loss_limit = (
            state.day_start_balance * self._limits.max_daily_loss_pct / _HUNDRED
        )
        if daily_loss >= daily_loss_limit:
            return "max_daily_loss"
        if state.consecutive_losses >= self._limits.max_consec_losses:
            return "max_consec_losses"
        return None
