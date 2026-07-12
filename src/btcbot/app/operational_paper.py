"""Fail-closed Gamma + CLOB WS + Chainlink loop for shared paper runtime.

This module only reads public market data and calls the paper-only composition
root. It contains no CLOB REST order client, signer, private key, or live path.
A round discovered too late is skipped because latest price is not a trustworthy
window start price.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from btcbot.adapters.chainlink import PriceUnavailableError
from btcbot.adapters.clock import Clock
from btcbot.app.paper_runtime import OperationalPaperRuntime
from btcbot.config.settings import Mode, Settings
from btcbot.data.store import Store
from btcbot.domain.fees import CryptoFeesV2
from btcbot.domain.models import (
    OrderBook,
    Outcome,
    PriceSource,
    RoundMeta,
    round_from_meta,
)
from btcbot.domain.signal import SignalEngine
from btcbot.domain.strategy import MarketBook
from btcbot.risk.manager import CircuitReason


class MarketDiscovery(Protocol):
    async def discover_active_round(self) -> RoundMeta: ...

    async def get_resolution(self, condition_id: str) -> Outcome | None: ...


class MarketStream(Protocol):
    def stream_market(self, token_ids: list[str]) -> AsyncIterator[OrderBook]: ...


@dataclass(frozen=True, slots=True)
class OperationalLoopConfig:
    tick_seconds: float = 0.25
    max_start_lag_seconds: float = 2.0
    resolution_poll_seconds: float = 2.0
    max_resolution_attempts: int = 90
    paper_execution_enabled: bool = False

    def __post_init__(self) -> None:
        if self.tick_seconds <= 0 or self.max_start_lag_seconds < 0:
            raise ValueError("invalid operational loop timing")
        if self.resolution_poll_seconds <= 0 or self.max_resolution_attempts <= 0:
            raise ValueError("invalid resolution polling timing")


@dataclass(frozen=True, slots=True)
class OperationalRoundReport:
    round_no: int
    ticks: int
    settled: bool
    skipped_reason: str | None = None


class LiveBookCache:
    """Latest normalized UP/DOWN books and PaperOMS BookProvider."""

    def __init__(self, token_up: str, token_down: str) -> None:
        self._token_up = token_up
        self._token_down = token_down
        self._books: dict[str, OrderBook] = {}
        self.ready = asyncio.Event()

    def update(self, book: OrderBook) -> None:
        if book.token_id not in {self._token_up, self._token_down}:
            return
        self._books[book.token_id] = book
        if self._token_up in self._books and self._token_down in self._books:
            self.ready.set()

    async def get_orderbook(self, token_id: str) -> OrderBook:
        try:
            return self._books[token_id]
        except KeyError as exc:
            raise RuntimeError(f"paper book not ready for token {token_id}") from exc

    def market_book(self) -> MarketBook | None:
        if not self.ready.is_set():
            return None
        return MarketBook(
            up=self._books[self._token_up],
            down=self._books[self._token_down],
        )


class OperationalPaperLoop:
    """Run one discovered market at a time against the shared paper core."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        settings: Settings,
        gamma: MarketDiscovery,
        stream: MarketStream,
        price_source: PriceSource,
        store: Store,
        clock: Clock,
        runtime: OperationalPaperRuntime,
        books: LiveBookCache,
        config: OperationalLoopConfig | None = None,
    ) -> None:
        if settings.mode is not Mode.PAPER or settings.live_confirmed == "yes":
            raise RuntimeError("operational loop is paper-only and requires LIVE_CONFIRMED=no")
        self._settings = settings
        self._gamma = gamma
        self._stream = stream
        self._price = price_source
        self._store = store
        self._clock = clock
        self._runtime = runtime
        self._books = books
        self._config = config or OperationalLoopConfig()
        self._signal = SignalEngine(
            fee_model=CryptoFeesV2(settings.fee_rate, settings.fee_exponent)
        )

    async def run_once(self, *, max_ticks: int | None = None) -> OperationalRoundReport:
        """Discover and process one round; ``max_ticks`` is for bounded smoke tests."""
        meta = await self._gamma.discover_active_round()
        return await self.run_round(meta, max_ticks=max_ticks)

    async def run_round(
        self, meta: RoundMeta, *, max_ticks: int | None = None
    ) -> OperationalRoundReport:
        now = self._clock.now()
        if now < meta.start_time:
            await asyncio.sleep((meta.start_time - now).total_seconds())
            now = self._clock.now()
        lag = (now - meta.start_time).total_seconds()
        round_no = int(meta.end_time.timestamp())
        if lag > self._config.max_start_lag_seconds:
            return OperationalRoundReport(round_no, 0, False, "late_start_price")

        try:
            start_tick = await self._price.price_now()
        except PriceUnavailableError as exc:
            await self._runtime.report_error(
                kind="Chainlink price unavailable",
                detail=type(exc).__name__,
                remediation="check Polygon RPC endpoints and Chainlink feed health",
            )
            self._runtime.risk.on_event(CircuitReason.PRICE_STALE)
            return OperationalRoundReport(round_no, 0, False, "start_price_unavailable")
        if start_tick.stale:
            self._runtime.risk.on_event(CircuitReason.PRICE_STALE)
            return OperationalRoundReport(round_no, 0, False, "start_price_stale")

        rnd = round_from_meta(meta, round_no=round_no, start_price=start_tick.price)
        await self._store.upsert_round(rnd)
        consumer = asyncio.create_task(self._consume_books(meta), name=f"paper-books-{round_no}")
        ticks = 0
        try:
            while self._clock.now() < meta.end_time:
                if max_ticks is not None and ticks >= max_ticks:
                    return OperationalRoundReport(round_no, ticks, False, "smoke_limit")
                await asyncio.sleep(self._config.tick_seconds)
                books = self._books.market_book()
                if books is None:
                    continue
                try:
                    price = await self._price.price_now()
                except PriceUnavailableError:
                    self._runtime.risk.on_event(CircuitReason.PRICE_STALE)
                    continue
                if price.stale:
                    self._runtime.risk.on_event(CircuitReason.PRICE_STALE)
                    continue
                self._runtime.risk.on_event(CircuitReason.PRICE_STALE, active=False)
                signal = self._signal.compute(
                    rnd,
                    price.price,
                    self._clock.now(),
                    self._settings.backtest_vol_per_sqrt_sec,
                    book_up=books.up,
                    book_down=books.down,
                )
                await self._store.insert_signal(signal, mode="paper")
                if self._config.paper_execution_enabled:
                    await self._runtime.on_tick(rnd, signal, books)
                ticks += 1
        finally:
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer

        outcome = await self._poll_resolution(meta.condition_id)
        if outcome is None:
            await self._runtime.report_error(
                kind="Gamma resolution timeout",
                detail=f"round {round_no} unresolved",
                remediation=("check Gamma API; keep entry halted until resolution is known"),
            )
            return OperationalRoundReport(round_no, ticks, False, "resolution_timeout")
        resolved = round_from_meta(meta, round_no=round_no, start_price=start_tick.price)
        resolved = type(resolved)(
            resolved.condition_id,
            resolved.round_no,
            resolved.token_id_up,
            resolved.token_id_down,
            resolved.window_start,
            resolved.window_end,
            resolved.start_price,
            resolved.tick_size,
            resolved.min_order_size,
            resolved.status,
            outcome,
        )
        await self._store.set_resolution(round_no, outcome, resolution_source="gamma")
        await self._runtime.settle(resolved)
        return OperationalRoundReport(round_no, ticks, True)

    async def _consume_books(self, meta: RoundMeta) -> None:
        self._runtime.set_wss_status("reconnecting")
        try:
            async for book in self._stream.stream_market([meta.token_id_up, meta.token_id_down]):
                self._books.update(book)
                self._runtime.set_wss_status("connected")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._runtime.set_wss_status("disconnected")
            await self._runtime.report_error(
                kind="WSS disconnected",
                detail=type(exc).__name__,
                remediation="check CLOB_WSS_URL and network connectivity",
            )

    async def _poll_resolution(self, condition_id: str) -> Outcome | None:
        for _ in range(self._config.max_resolution_attempts):
            outcome = await self._gamma.get_resolution(condition_id)
            if outcome is not None:
                return outcome
            await asyncio.sleep(self._config.resolution_poll_seconds)
        return None
