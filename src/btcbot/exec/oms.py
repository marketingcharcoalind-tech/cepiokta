"""Risk-gated paper order management system for Prompt 2.2.

This module simulates taker fills against an injected current order book. It has
no CLOB REST client, signer, private key, API credential, or live-order path.
Every new paper order is checked by :class:`RiskManager` before book access.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from btcbot.adapters.clock import Clock
from btcbot.config.settings import Mode
from btcbot.domain.models import Fill, OrderAck, OrderBook
from btcbot.risk.manager import RiskDecision, RiskManager, RiskOrder, RiskState, Veto

_ZERO = Decimal("0")
_ONE = Decimal("1")
_EPSILON = Decimal("1e-9")


class BookProvider(Protocol):
    """Read-only source of the latest book for one outcome token."""

    async def get_orderbook(self, token_id: str) -> OrderBook:
        """Return the latest normalized order book without placing an order."""
        ...


@dataclass(frozen=True, slots=True)
class PaperOMSConfig:
    """Execution assumptions for paper fills, not live configuration."""

    latency_ms: int = 100
    competition_fraction: Decimal = _ZERO

    def __post_init__(self) -> None:
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if not (_ZERO <= self.competition_fraction < _ONE):
            raise ValueError("competition_fraction must be in [0, 1)")


@dataclass(frozen=True, slots=True)
class PaperExecution:
    """Immutable result of one idempotent paper submission."""

    ack: OrderAck
    fills: tuple[Fill, ...]
    risk_decision: RiskDecision
    reason: str


class PaperOMS:
    """Paper-only OMS with idempotency, latency, fill simulation, and risk veto."""

    def __init__(
        self,
        *,
        mode: Mode,
        risk_manager: RiskManager,
        books: BookProvider,
        clock: Clock,
        config: PaperOMSConfig | None = None,
    ) -> None:
        if mode is not Mode.PAPER:
            raise ValueError("PaperOMS requires MODE=paper")
        self._risk = risk_manager
        self._books = books
        self._clock = clock
        self._config = config or PaperOMSConfig()
        self._results: dict[str, PaperExecution] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def submit(self, order: RiskOrder, state: RiskState) -> PaperExecution:
        """Risk-check and simulate one order; retries by client ID are idempotent."""
        client_id = order.request.client_id
        if not client_id:
            raise ValueError("client_id must not be empty")
        cached = self._results.get(client_id)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(client_id, asyncio.Lock())
        async with lock:
            cached = self._results.get(client_id)
            if cached is not None:
                return cached
            result = await self._submit_once(order, state)
            self._results[client_id] = result
            return result

    async def _submit_once(self, order: RiskOrder, state: RiskState) -> PaperExecution:
        decision = self._risk.check(order, state)
        if isinstance(decision, Veto):
            return self._result(order, "REJECTED", decision, f"risk:{decision.reason}")

        request = order.request
        if request.order_type not in {"FOK", "FAK"}:
            return self._result(order, "REJECTED", decision, "unsupported_paper_order_type")

        await asyncio.sleep(self._config.latency_ms / 1000)
        book = await self._books.get_orderbook(request.token_id)
        self._validate_book(book, request.token_id)
        filled_size, average_price = self._match(book, order)
        if filled_size <= _ZERO:
            return self._result(order, "REJECTED", decision, "no_fill")

        status = "FILLED" if filled_size + _EPSILON >= request.size else "PARTIALLY_FILLED"
        order_id = self._paper_order_id(request.client_id)
        fill = Fill(
            order_id=order_id,
            token_id=request.token_id,
            price=average_price,
            size=filled_size,
            ts=self._utc_now(),
        )
        return self._result(order, status, decision, "filled", fills=(fill,))

    def _match(self, book: OrderBook, order: RiskOrder) -> tuple[Decimal, Decimal]:
        request = order.request
        is_buy = request.side == "BUY"
        levels = sorted(
            book.asks if is_buy else book.bids,
            key=lambda level: level.price,
            reverse=not is_buy,
        )
        remaining = request.size
        filled = _ZERO
        cost = _ZERO
        available_factor = _ONE - self._config.competition_fraction
        for level in levels:
            within_limit = level.price <= request.price if is_buy else level.price >= request.price
            if not within_limit:
                break
            available = level.size * available_factor
            take = min(remaining, max(_ZERO, available))
            filled += take
            cost += take * level.price
            remaining -= take
            if remaining <= _EPSILON:
                break
        if request.order_type == "FOK" and filled + _EPSILON < request.size:
            return _ZERO, _ZERO
        if filled <= _ZERO:
            return _ZERO, _ZERO
        return filled, cost / filled

    def _result(
        self,
        order: RiskOrder,
        status: str,
        decision: RiskDecision,
        reason: str,
        *,
        fills: tuple[Fill, ...] = (),
    ) -> PaperExecution:
        request = order.request
        ack = OrderAck(
            client_id=request.client_id,
            order_id=self._paper_order_id(request.client_id),
            status=status,
            ts=self._utc_now(),
        )
        return PaperExecution(ack=ack, fills=fills, risk_decision=decision, reason=reason)

    def _utc_now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("PaperOMS clock must return timezone-aware time")
        return now.astimezone(UTC)

    @staticmethod
    def _validate_book(book: OrderBook, token_id: str) -> None:
        if book.token_id != token_id:
            raise ValueError("book token does not match order token")
        if book.ts.tzinfo is None or book.ts.utcoffset() is None:
            raise ValueError("book timestamp must be timezone-aware")

    @staticmethod
    def _paper_order_id(client_id: str) -> str:
        return f"paper:{client_id}"
