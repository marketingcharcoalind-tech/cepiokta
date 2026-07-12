from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from btcbot.adapters.clock import SimClock
from btcbot.app.paper import PaperLedger, PaperRunner
from btcbot.config.settings import Mode
from btcbot.data.store import Store
from btcbot.domain.fees import CryptoFeesV2
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus, Signal
from btcbot.domain.strategy import MarketBook, Strategy, StrategyParams
from btcbot.exec.oms import PaperOMS, PaperOMSConfig
from btcbot.exec.sizing import SizingLimits
from btcbot.risk.manager import RiskLimits, RiskManager

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class Books:
    def __init__(self, market: MarketBook) -> None:
        self.market = market

    async def get_orderbook(self, token_id: str) -> OrderBook:
        return self.market.up if token_id == "up" else self.market.down


def _market() -> MarketBook:
    return MarketBook(
        up=OrderBook("up", NOW, [], [BookLevel(Decimal("0.96"), Decimal("100"))]),
        down=OrderBook("down", NOW, [], [BookLevel(Decimal("0.04"), Decimal("100"))]),
    )


def _round(outcome: Outcome | None = Outcome.UP) -> Round:
    return Round(
        "condition",
        1,
        "up",
        "down",
        NOW - timedelta(minutes=5),
        NOW,
        Decimal("100000"),
        Decimal("0.01"),
        Decimal("1"),
        RoundStatus.RESOLVED if outcome else RoundStatus.ACTIVE,
        outcome,
    )


def _signal() -> Signal:
    return Signal(
        1,
        NOW,
        Decimal("100100"),
        Decimal("100"),
        30.0,
        Decimal("0.999"),
        "UP",
        Decimal("0.96"),
        Decimal("0.03"),
    )


def _strategy() -> Strategy:
    return Strategy(
        StrategyParams(
            60,
            Decimal("50"),
            Decimal("0.96"),
            Decimal("0.99"),
            Decimal("0.01"),
            Decimal("0.90"),
            Decimal("0.5"),
            Decimal("0.65"),
        )
    )


def _sizing() -> SizingLimits:
    return SizingLimits(
        Decimal("0.25"),
        Decimal("5"),
        Decimal("0.02"),
        Decimal("0.8"),
        Decimal("0.01"),
        Decimal("0.99"),
        Decimal("1"),
        Decimal("0.01"),
    )


def _risk(clock: SimClock) -> RiskManager:
    limits = RiskLimits(
        Decimal("5"), Decimal("10"), Decimal("5"), 5, Decimal("50"), 30
    )
    return RiskManager(limits, clock)


@pytest.fixture
async def setup_runner(
    tmp_path: Path,
) -> AsyncGenerator[tuple[PaperRunner, PaperLedger, Store, MarketBook]]:
    clock = SimClock(NOW)
    market = _market()
    store = await Store.open(str(tmp_path / "paper.db"))
    fee = CryptoFeesV2()
    ledger = PaperLedger(Decimal("500"), fee)
    oms = PaperOMS(
        mode=Mode.PAPER,
        risk_manager=_risk(clock),
        books=Books(market),
        clock=clock,
        config=PaperOMSConfig(latency_ms=0),
    )
    runner = PaperRunner(
        strategy=_strategy(),
        limits=_sizing(),
        oms=oms,
        ledger=ledger,
        store=store,
        clock=clock,
    )
    try:
        yield runner, ledger, store, market
    finally:
        await store.close()


async def test_entry_flows_through_oms_and_persists(
    setup_runner: tuple[PaperRunner, PaperLedger, Store, MarketBook],
) -> None:
    runner, ledger, store, market = setup_runner
    tick = await runner.on_tick(_round(None), _signal(), market)
    assert tick.execution is not None
    assert tick.execution.ack.status == "FILLED"
    assert ledger.position(1, "up") is not None
    order = await store.get_order(tick.execution.ack.client_id)
    fills = await store.get_fills(tick.execution.ack.order_id)
    assert order is not None
    assert order.mode == "paper"
    assert len(fills) == 1


async def test_settlement_is_net_of_fee_and_persists_equity(
    setup_runner: tuple[PaperRunner, PaperLedger, Store, MarketBook],
) -> None:
    runner, ledger, store, market = setup_runner
    await runner.on_tick(_round(None), _signal(), market)
    before = ledger.balance
    result = await runner.settle(_round(Outcome.UP))
    assert result.pnl > Decimal("0")
    assert result.balance_after > before
    assert ledger.fees_paid > Decimal("0")
    stored = await store.get_round_result(1)
    equity = await store.get_equity_curve("paper")
    assert stored == result
    assert len(equity) == 1


async def test_losing_settlement_reduces_balance(
    setup_runner: tuple[PaperRunner, PaperLedger, Store, MarketBook],
) -> None:
    runner, _ledger, _store, market = setup_runner
    await runner.on_tick(_round(None), _signal(), market)
    result = await runner.settle(_round(Outcome.DOWN))
    assert result.pnl < Decimal("0")
    assert result.balance_after < Decimal("500")


async def test_no_signal_produces_no_order(
    setup_runner: tuple[PaperRunner, PaperLedger, Store, MarketBook],
) -> None:
    runner, _ledger, store, market = setup_runner
    weak = Signal(
        1,
        NOW,
        Decimal("100001"),
        Decimal("1"),
        30.0,
        Decimal("0.5"),
        "UP",
        Decimal("0.96"),
        Decimal("-0.46"),
    )
    tick = await runner.on_tick(_round(None), weak, market)
    assert tick.execution is None
    assert await store.get_order("paper:1:1") is None


async def test_unresolved_round_cannot_settle(
    setup_runner: tuple[PaperRunner, PaperLedger, Store, MarketBook],
) -> None:
    runner, _ledger, _store, _market_book = setup_runner
    with pytest.raises(ValueError, match="Gamma"):
        await runner.settle(_round(None))
