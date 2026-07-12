import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import AsyncIterator

from btcbot.adapters.clock import SimClock
from btcbot.app.operational_paper import (
    LiveBookCache,
    OperationalLoopConfig,
    OperationalPaperLoop,
)
from btcbot.app.paper_runtime import build_operational_paper_runtime
from btcbot.config.settings import Mode, Settings
from btcbot.data.store import Store
from btcbot.domain.models import (
    BookLevel,
    MarketStatus,
    OrderBook,
    Outcome,
    PriceTick,
    RoundMeta,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class Price:
    def __init__(self) -> None:
        self.calls = 0

    async def price_now(self) -> PriceTick:
        self.calls += 1
        return PriceTick(
            Decimal("100000") + self.calls * Decimal("100"),
            NOW,
            "fake",
            self.calls,
            False,
        )


class Gamma:
    def __init__(self, meta: RoundMeta) -> None:
        self.meta = meta

    async def discover_active_round(self) -> RoundMeta:
        return self.meta

    async def get_resolution(self, condition_id: str) -> Outcome | None:
        return Outcome.UP


class Stream:
    async def stream_market(self, token_ids: list[str]) -> AsyncIterator[OrderBook]:
        yield OrderBook(
            token_ids[0], NOW, [], [BookLevel(Decimal("0.96"), Decimal("100"))]
        )
        yield OrderBook(
            token_ids[1], NOW, [], [BookLevel(Decimal("0.04"), Decimal("100"))]
        )
        await asyncio.Event().wait()
        if False:
            yield OrderBook(token_ids[0], NOW, [], [])


def _settings() -> Settings:
    return Settings(
        mode=Mode.PAPER,
        live_confirmed="no",
        delta_threshold="50",
        t_entry_sec=60,
        min_price=Decimal("0.96"),
        max_price=Decimal("0.99"),
        paper_starting_balance=Decimal("500"),
    )


def _meta(start: datetime = NOW) -> RoundMeta:
    return RoundMeta(
        "market",
        "condition",
        "btc-updown-5m-1",
        "up",
        "down",
        start,
        start + timedelta(minutes=5),
        Decimal("0.01"),
        Decimal("1"),
        MarketStatus.OPEN,
    )


async def test_bounded_smoke_writes_only_paper_db(tmp_path: Path) -> None:
    settings = _settings()
    store = await Store.open(str(tmp_path / "paper.db"))
    clock = SimClock(NOW)
    cache = LiveBookCache("up", "down")
    runtime = build_operational_paper_runtime(
        settings=settings,
        store=store,
        books=cache,
        clock=clock,
    )
    loop = OperationalPaperLoop(
        settings=settings,
        gamma=Gamma(_meta()),
        stream=Stream(),
        price_source=Price(),
        store=store,
        clock=clock,
        runtime=runtime,
        books=cache,
        config=OperationalLoopConfig(tick_seconds=0.001),
    )
    try:
        report = await loop.run_once(max_ticks=1)
        assert report.skipped_reason == "smoke_limit"
        assert report.ticks == 1
        assert await store.get_round(report.round_no) is not None
        assert len(await store.get_signals(report.round_no)) == 1
        assert (await runtime.source.status()).wss_status == "connected"
    finally:
        await store.close()


async def test_late_round_is_skipped_without_price_or_orders(tmp_path: Path) -> None:
    settings = _settings()
    store = await Store.open(str(tmp_path / "paper.db"))
    clock = SimClock(NOW)
    cache = LiveBookCache("up", "down")
    price = Price()
    runtime = build_operational_paper_runtime(
        settings=settings,
        store=store,
        books=cache,
        clock=clock,
    )
    loop = OperationalPaperLoop(
        settings=settings,
        gamma=Gamma(_meta(NOW - timedelta(seconds=10))),
        stream=Stream(),
        price_source=price,
        store=store,
        clock=clock,
        runtime=runtime,
        books=cache,
        config=OperationalLoopConfig(max_start_lag_seconds=2),
    )
    try:
        report = await loop.run_once(max_ticks=1)
        assert report.skipped_reason == "late_start_price"
        assert price.calls == 0
        assert await store.get_round(report.round_no) is None
    finally:
        await store.close()
