"""Paper runner and net-of-fee ledger for Prompt 2.3.

The runner wires domain decisions, sizing, RiskManager, PaperOMS, and Store while
remaining strictly paper-only. It exposes ``on_tick`` for a realtime adapter loop
and ``settle`` for Gamma-labelled round settlement. No live order API exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcbot.adapters.clock import Clock
from btcbot.data.store import OrderRow, Store
from btcbot.domain.fees import FeeModel
from btcbot.domain.models import OrderRequest, Outcome, Position, Round, RoundResult, Signal
from btcbot.domain.strategy import EnterOrder, Exit, Hedge, MarketBook, NoOp, Strategy
from btcbot.exec.oms import PaperExecution, PaperOMS
from btcbot.exec.sizing import SizingLimits, size
from btcbot.risk.manager import RiskAction, RiskOrder, RiskState

_ZERO = Decimal("0")


@dataclass(slots=True)
class PaperPosition:
    """Mutable paper position for one token inside one round."""

    round_no: int
    token_id: str
    outcome: Outcome
    size: Decimal = _ZERO
    cost: Decimal = _ZERO

    @property
    def average_price(self) -> Decimal:
        return self.cost / self.size if self.size > _ZERO else _ZERO


@dataclass(frozen=True, slots=True)
class PaperTickResult:
    """Observable result of one paper tick."""

    decision: str
    execution: PaperExecution | None
    balance: Decimal


class PaperLedger:
    """Cash, positions, fees, and per-round starting equity using Decimal."""

    def __init__(self, starting_balance: Decimal, fee_model: FeeModel) -> None:
        if starting_balance <= _ZERO:
            raise ValueError("starting_balance must be positive")
        self.balance = starting_balance
        self._fee_model = fee_model
        self._positions: dict[tuple[int, str], PaperPosition] = {}
        self._round_start: dict[int, Decimal] = {}
        self.fees_paid = _ZERO

    def start_round(self, round_no: int) -> None:
        self._round_start.setdefault(round_no, self.balance)

    def position(self, round_no: int, token_id: str) -> PaperPosition | None:
        position = self._positions.get((round_no, token_id))
        return position if position is not None and position.size > _ZERO else None

    def domain_position(self, round_no: int) -> Position | None:
        positions = [
            position
            for (position_round, _), position in self._positions.items()
            if position_round == round_no and position.size > _ZERO
        ]
        if len(positions) != 1:
            return None
        position = positions[0]
        return Position(round_no, position.token_id, position.size, position.average_price)

    def open_exposure(self) -> Decimal:
        return sum((position.cost for position in self._positions.values()), _ZERO)

    def round_notional(self, round_no: int) -> Decimal:
        return sum(
            (position.cost for position in self._positions.values() if position.round_no == round_no),
            _ZERO,
        )

    def apply(self, round_no: int, outcome: Outcome, side: str, execution: PaperExecution) -> None:
        for fill in execution.fills:
            fee = self._fee_model.fee_per_share(fill.price) * fill.size
            self.fees_paid += fee
            key = (round_no, fill.token_id)
            position = self._positions.setdefault(
                key, PaperPosition(round_no=round_no, token_id=fill.token_id, outcome=outcome)
            )
            if side == "BUY":
                debit = fill.price * fill.size + fee
                if debit > self.balance:
                    raise RuntimeError("paper fill exceeds available balance")
                self.balance -= debit
                position.size += fill.size
                position.cost += fill.price * fill.size + fee
            else:
                if fill.size > position.size:
                    raise RuntimeError("paper sell exceeds position")
                average_cost = position.average_price
                self.balance += fill.price * fill.size - fee
                position.size -= fill.size
                position.cost -= average_cost * fill.size

    def settle(self, rnd: Round) -> RoundResult:
        if rnd.resolved_outcome is None:
            raise ValueError("round must have Gamma resolved_outcome")
        self.start_round(rnd.round_no)
        positions = [
            position
            for (round_no, _), position in self._positions.items()
            if round_no == rnd.round_no and position.size > _ZERO
        ]
        payout = sum(
            (position.size for position in positions if position.outcome is rnd.resolved_outcome),
            _ZERO,
        )
        self.balance += payout
        total_size = sum((position.size for position in positions), _ZERO)
        total_cost = sum((position.cost for position in positions), _ZERO)
        primary = max(positions, key=lambda item: item.cost) if positions else None
        for position in positions:
            position.size = _ZERO
            position.cost = _ZERO
        pnl = self.balance - self._round_start[rnd.round_no]
        return RoundResult(
            round_no=rnd.round_no,
            side_taken=primary.outcome.value if primary is not None else "NONE",
            entry_price=primary.average_price if primary is not None else _ZERO,
            size=total_size,
            hedge_cost=max(_ZERO, total_cost - (primary.cost if primary is not None else _ZERO)),
            settled=payout,
            pnl=pnl,
            balance_after=self.balance,
        )


class PaperRunner:
    """One-market paper orchestration core called by a realtime data loop."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        limits: SizingLimits,
        oms: PaperOMS,
        ledger: PaperLedger,
        store: Store,
        clock: Clock,
    ) -> None:
        self._strategy = strategy
        self._limits = limits
        self._oms = oms
        self._ledger = ledger
        self._store = store
        self._clock = clock
        self._sequence = 0
        self._recent_orders: list = []
        self._consecutive_losses = 0
        self._day_start_balance = ledger.balance

    async def on_tick(self, rnd: Round, signal: Signal, books: MarketBook) -> PaperTickResult:
        """Evaluate one realtime tick and execute at most one paper decision."""
        self._ledger.start_round(rnd.round_no)
        position = self._ledger.domain_position(rnd.round_no)
        decision = self._strategy.on_tick(signal, books, position)[0]
        if isinstance(decision, NoOp):
            return PaperTickResult(decision.reason, None, self._ledger.balance)

        request, action, outcome = self._build_order(rnd, signal, books, position, decision)
        if request is None:
            return PaperTickResult("size_zero", None, self._ledger.balance)
        risk_state = RiskState(
            balance=self._ledger.balance,
            day_start_balance=self._day_start_balance,
            open_exposure=self._ledger.open_exposure(),
            round_notional=self._ledger.round_notional(rnd.round_no),
            consecutive_losses=self._consecutive_losses,
            recent_order_timestamps=tuple(self._recent_orders),
        )
        execution = await self._oms.submit(
            RiskOrder(request=request, round_no=rnd.round_no, action=action), risk_state
        )
        await self._persist(rnd.round_no, request, execution)
        self._recent_orders.append(execution.ack.ts)
        if execution.fills:
            self._ledger.apply(rnd.round_no, outcome, request.side, execution)
        return PaperTickResult(decision.reason, execution, self._ledger.balance)

    async def settle(self, rnd: Round) -> RoundResult:
        """Settle via Gamma outcome, persist result and equity, update loss streak."""
        result = self._ledger.settle(rnd)
        self._consecutive_losses = self._consecutive_losses + 1 if result.pnl < _ZERO else 0
        await self._store.insert_round_result(result, mode="paper")
        await self._store.insert_equity_point(self._clock.now(), result.balance_after, "paper")
        return result

    def _build_order(self, rnd, signal, books, position, decision):  # type: ignore[no-untyped-def]
        self._sequence += 1
        client_id = f"paper:{rnd.round_no}:{self._sequence}"
        if isinstance(decision, EnterOrder):
            depth = sum((level.size for level in books.for_outcome(Outcome(decision.outcome)).asks), _ZERO)
            order_size = size(signal, self._ledger.balance, depth, self._limits)
            action = RiskAction.ENTRY
        elif isinstance(decision, Hedge):
            order_size = position.size * decision.hedge_fraction if position is not None else _ZERO
            action = RiskAction.HEDGE
        elif isinstance(decision, Exit):
            order_size = position.size if position is not None else _ZERO
            action = RiskAction.EXIT
        else:
            return None, RiskAction.ENTRY, Outcome.UP
        if order_size <= _ZERO:
            return None, action, Outcome(decision.outcome)
        request = OrderRequest(
            client_id=client_id,
            token_id=decision.token_id,
            side=decision.side,
            price=decision.price,
            size=order_size,
            order_type=decision.order_type,
        )
        return request, action, Outcome(decision.outcome)

    async def _persist(
        self, round_no: int, request: OrderRequest, execution: PaperExecution
    ) -> None:
        await self._store.insert_order(
            OrderRow(
                client_id=request.client_id,
                order_id=execution.ack.order_id,
                round_no=round_no,
                token_id=request.token_id,
                side=request.side,
                price=request.price,
                size=request.size,
                order_type=request.order_type,
                status=execution.ack.status,
                mode="paper",
                created_at=execution.ack.ts,
            )
        )
        for fill in execution.fills:
            await self._store.insert_fill(fill)
