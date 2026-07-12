"""Paper order/fill/position/settlement reconciliation for Prompt 2.4.

A mismatch is safety-critical: reconciliation latches the RiskManager mismatch
breaker and emits a critical notification. The module is paper/read-only with
respect to external venues and has no live execution dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from btcbot.adapters.telegram import BotEvent, Severity
from btcbot.domain.models import Fill, OrderRequest, Outcome, RoundResult
from btcbot.risk.manager import CircuitReason, RiskManager

_ZERO = Decimal("0")
_EPSILON = Decimal("1e-9")


class EventNotifier(Protocol):
    async def emit(self, event: BotEvent) -> None:
        """Queue a notification without blocking core processing."""
        ...


@dataclass(frozen=True, slots=True)
class PaperOrderRecord:
    """One paper request and its immutable OMS outcome."""

    request: OrderRequest
    order_id: str
    status: str
    fills: tuple[Fill, ...]


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Position state immediately before Gamma settlement."""

    token_id: str
    outcome: Outcome
    size: Decimal


@dataclass(frozen=True, slots=True)
class ReconciliationSnapshot:
    """All facts required to reconcile one settled paper round."""

    round_no: int
    resolved_outcome: Outcome
    orders: tuple[PaperOrderRecord, ...]
    positions: tuple[PositionSnapshot, ...]
    result: RoundResult
    round_start_balance: Decimal
    actual_balance: Decimal
    ts: datetime


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Deterministic reconciliation result."""

    ok: bool
    mismatches: tuple[str, ...]


class PaperReconciler:
    """Validate paper accounting and freeze RiskManager on any mismatch."""

    def __init__(self, risk: RiskManager, notifier: EventNotifier) -> None:
        self._risk = risk
        self._notifier = notifier

    async def reconcile(self, snapshot: ReconciliationSnapshot) -> ReconciliationReport:
        """Reconcile one round and alert/freeze on mismatch."""
        mismatches = self._find_mismatches(snapshot)
        report = ReconciliationReport(ok=not mismatches, mismatches=tuple(mismatches))
        if mismatches:
            self._risk.on_event(CircuitReason.RECONCILIATION_MISMATCH)
            await self._notifier.emit(
                BotEvent(
                    kind="reconciliation_mismatch",
                    text=(
                        f"Round {snapshot.round_no} reconciliation mismatch: "
                        + "; ".join(mismatches)
                    ),
                    severity=Severity.CRITICAL,
                    ts=snapshot.ts,
                )
            )
        return report

    @staticmethod
    def _find_mismatches(  # noqa: PLR0912
        snapshot: ReconciliationSnapshot,
    ) -> list[str]:
        mismatches: list[str] = []
        if snapshot.ts.tzinfo is None or snapshot.ts.utcoffset() is None:
            mismatches.append("naive_timestamp")
        if snapshot.result.round_no != snapshot.round_no:
            mismatches.append("round_result_round_no")

        net_sizes: dict[str, Decimal] = {}
        token_outcomes = {position.token_id: position.outcome for position in snapshot.positions}
        for record in snapshot.orders:
            request = record.request
            filled = _ZERO
            for fill in record.fills:
                if fill.order_id != record.order_id:
                    mismatches.append(f"unknown_order_fill:{fill.order_id}")
                if fill.token_id != request.token_id:
                    mismatches.append(f"fill_token:{record.order_id}")
                if fill.price <= _ZERO or fill.size <= _ZERO:
                    mismatches.append(f"invalid_fill:{record.order_id}")
                filled += fill.size
            if filled > request.size + _EPSILON:
                mismatches.append(f"overfill:{record.order_id}")
            if record.status == "FILLED" and abs(filled - request.size) > _EPSILON:
                mismatches.append(f"filled_status_size:{record.order_id}")
            direction = Decimal("1") if request.side == "BUY" else Decimal("-1")
            net_sizes[request.token_id] = (
                net_sizes.get(request.token_id, _ZERO) + direction * filled
            )

        positions_by_token = {position.token_id: position.size for position in snapshot.positions}
        for token_id in set(net_sizes) | set(positions_by_token):
            actual_size = net_sizes.get(token_id, _ZERO)
            recorded_size = positions_by_token.get(token_id, _ZERO)
            if abs(actual_size - recorded_size) > _EPSILON:
                mismatches.append(f"position_size:{token_id}")

        expected_settlement = sum(
            (
                size
                for token_id, size in positions_by_token.items()
                if token_outcomes.get(token_id) is snapshot.resolved_outcome
            ),
            _ZERO,
        )
        if abs(snapshot.result.settled - expected_settlement) > _EPSILON:
            mismatches.append("settlement_payout")
        if abs(snapshot.result.balance_after - snapshot.actual_balance) > _EPSILON:
            mismatches.append("balance_after")
        expected_pnl = snapshot.actual_balance - snapshot.round_start_balance
        if abs(snapshot.result.pnl - expected_pnl) > _EPSILON:
            mismatches.append("round_pnl")
        return mismatches
