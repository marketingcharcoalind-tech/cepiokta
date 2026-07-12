from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from btcbot.adapters.clock import SimClock
from btcbot.app.operational_paper import LiveBookCache, OperationalPaperLoop
from btcbot.app.paper_runtime import build_operational_paper_runtime
from btcbot.config.settings import Mode, Settings
from btcbot.data.store import Store
from btcbot.domain.models import (
    BookLevel,
    MarketStatus,
    OrderBook,
    Outcome,
    Position,
    PriceTick,
    RoundMeta,
    RoundResult,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class Gamma:
    async def discover_active_round(self) -> RoundMeta:
        raise NotImplementedError

    async def get_resolution(self, condition_id: str) -> Outcome | None:
        return Outcome.UP


class Stream:
    def stream_market(self, token_ids: list[str]):
        raise NotImplementedError


class Price:
    async def price_now(self) -> PriceTick:
        return PriceTick(Decimal("100000"), NOW, "fake", 1, False)


def _settings() -> Settings:
    return Settings(
        mode=Mode.PAPER,
        live_confirmed="no",
        delta_threshold="50",
        paper_starting_balance=Decimal("500"),
    )


def _meta() -> RoundMeta:
    return RoundMeta(
        "market",
        "condition",
        "btc-updown-5m-1",
        "up",
        "down",
        NOW,
        NOW + timedelta(minutes=5),
        Decimal("0.01"),
        Decimal("1"),
        MarketStatus.OPEN,
    )


async def test_empty_settlement_reconciles_cleanly(tmp_path: Path) -> None:
    store = await Store.open(str(tmp_path / "paper.db"))
    cache = LiveBookCache("up", "down")
    clock = SimClock(NOW)
    runtime = build_operational_paper_runtime(
        settings=_settings(), store=store, books=cache, clock=clock
    )
    loop = OperationalPaperLoop(
        settings=_settings(),
        gamma=Gamma(),
        stream=Stream(),
        price_source=Price(),
        store=store,
        clock=clock,
        runtime=runtime,
        books=cache,
    )
    result = RoundResult(
        round_no=1,
        side_taken="NONE",
        entry_price=Decimal("0"),
        size=Decimal("0"),
        hedge_cost=Decimal("0"),
        settled=Decimal("0"),
        pnl=Decimal("0"),
        balance_after=Decimal("500"),
    )
    try:
        report = await loop._reconcile_settlement(
            meta=_meta(),
            result=result,
            positions=(),
            client_ids=[],
            resolved_outcome=Outcome.UP,
        )
        assert report.ok is True
        assert runtime.risk.killed is False
    finally:
        await store.close()


def test_position_snapshot_maps_token_to_gamma_outcome() -> None:
    snapshots = OperationalPaperLoop._position_snapshots(
        _meta(),
        (Position(1, "up", Decimal("2"), Decimal("0.96")),),
    )
    assert snapshots[0].outcome is Outcome.UP
    assert snapshots[0].size == Decimal("2")
