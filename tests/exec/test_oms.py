from datetime import UTC, datetime
from decimal import Decimal

import pytest

from btcbot.adapters.clock import SimClock
from btcbot.config.settings import Mode
from btcbot.domain.models import BookLevel, OrderBook, OrderRequest
from btcbot.exec.oms import PaperOMS, PaperOMSConfig
from btcbot.risk.manager import (
    Allow,
    CircuitReason,
    RiskAction,
    RiskLimits,
    RiskManager,
    RiskOrder,
    RiskState,
    Veto,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class FakeBooks:
    def __init__(self, book: OrderBook) -> None:
        self.book = book
        self.calls = 0

    async def get_orderbook(self, token_id: str) -> OrderBook:
        self.calls += 1
        return self.book


def _book(*, asks: list[tuple[str, str]], bids: list[tuple[str, str]] | None = None) -> OrderBook:
    return OrderBook(
        token_id="up",
        ts=NOW,
        asks=[BookLevel(Decimal(price), Decimal(size)) for price, size in asks],
        bids=[BookLevel(Decimal(price), Decimal(size)) for price, size in bids or []],
    )


def _risk() -> RiskManager:
    limits = RiskLimits(
        max_notional_round=Decimal("5"),
        max_open_exposure=Decimal("10"),
        max_daily_loss_pct=Decimal("5"),
        max_consec_losses=5,
        min_balance=Decimal("50"),
        max_orders_per_min=30,
    )
    return RiskManager(limits, SimClock(NOW))


def _state(**changes: object) -> RiskState:
    values: dict[str, object] = {
        "balance": Decimal("100"),
        "day_start_balance": Decimal("100"),
        "open_exposure": Decimal("0"),
        "round_notional": Decimal("0"),
        "consecutive_losses": 0,
    }
    values.update(changes)
    return RiskState(**values)  # type: ignore[arg-type]


def _order(
    *,
    client_id: str = "paper-1",
    side: str = "BUY",
    price: str = "0.97",
    size: str = "3",
    order_type: str = "FOK",
) -> RiskOrder:
    request = OrderRequest(client_id, "up", side, Decimal(price), Decimal(size), order_type)
    return RiskOrder(request=request, round_no=1, action=RiskAction.ENTRY)


def _oms(book: OrderBook, *, config: PaperOMSConfig | None = None) -> tuple[PaperOMS, FakeBooks]:
    books = FakeBooks(book)
    oms = PaperOMS(
        mode=Mode.PAPER,
        risk_manager=_risk(),
        books=books,
        clock=SimClock(NOW),
        config=config or PaperOMSConfig(latency_ms=0),
    )
    return oms, books


def test_rejects_any_non_paper_mode() -> None:
    books = FakeBooks(_book(asks=[]))
    with pytest.raises(ValueError, match="MODE=paper"):
        PaperOMS(
            mode=Mode.READONLY,
            risk_manager=_risk(),
            books=books,
            clock=SimClock(NOW),
        )


async def test_fok_walks_levels_and_fills_all() -> None:
    oms, _ = _oms(_book(asks=[("0.96", "2"), ("0.97", "2")]))
    result = await oms.submit(_order(), _state())
    assert isinstance(result.risk_decision, Allow)
    assert result.ack.status == "FILLED"
    assert result.fills[0].size == Decimal("3")
    assert result.fills[0].price == Decimal("0.9633333333333333333333333333")


async def test_fok_is_all_or_nothing() -> None:
    oms, _ = _oms(_book(asks=[("0.96", "2")]))
    result = await oms.submit(_order(size="3"), _state())
    assert result.ack.status == "REJECTED"
    assert result.reason == "no_fill"
    assert result.fills == ()


async def test_fak_allows_partial_fill() -> None:
    oms, _ = _oms(_book(asks=[("0.96", "2")]))
    result = await oms.submit(_order(size="3", order_type="FAK"), _state())
    assert result.ack.status == "PARTIALLY_FILLED"
    assert result.fills[0].size == Decimal("2")


async def test_sell_walks_bids_descending() -> None:
    oms, _ = _oms(_book(asks=[], bids=[("0.95", "1"), ("0.94", "2")]))
    result = await oms.submit(_order(side="SELL", price="0.94"), _state())
    assert result.ack.status == "FILLED"
    assert result.fills[0].price == Decimal("0.9433333333333333333333333333")


async def test_price_outside_limit_does_not_fill() -> None:
    oms, _ = _oms(_book(asks=[("0.98", "100")]))
    result = await oms.submit(_order(price="0.97"), _state())
    assert result.ack.status == "REJECTED"
    assert result.reason == "no_fill"


async def test_competition_reduces_available_depth() -> None:
    config = PaperOMSConfig(latency_ms=0, competition_fraction=Decimal("0.5"))
    oms, _ = _oms(_book(asks=[("0.96", "4")]), config=config)
    result = await oms.submit(_order(size="3", order_type="FAK"), _state())
    assert result.fills[0].size == Decimal("2")


async def test_risk_veto_happens_before_book_access() -> None:
    oms, books = _oms(_book(asks=[("0.96", "100")]))
    result = await oms.submit(_order(size="6", price="1"), _state())
    assert isinstance(result.risk_decision, Veto)
    assert result.reason == "risk:max_notional_round"
    assert books.calls == 0


async def test_circuit_breaker_vetoes_before_book_access() -> None:
    book = _book(asks=[("0.96", "100")])
    books = FakeBooks(book)
    risk = _risk()
    risk.on_event(CircuitReason.PRICE_STALE)
    oms = PaperOMS(
        mode=Mode.PAPER,
        risk_manager=risk,
        books=books,
        clock=SimClock(NOW),
        config=PaperOMSConfig(latency_ms=0),
    )
    result = await oms.submit(_order(), _state())
    assert isinstance(result.risk_decision, Veto)
    assert books.calls == 0


async def test_duplicate_client_id_is_idempotent() -> None:
    oms, books = _oms(_book(asks=[("0.96", "100")]))
    first = await oms.submit(_order(), _state())
    second = await oms.submit(_order(), _state())
    assert second is first
    assert books.calls == 1


async def test_rejects_unsupported_gtc_without_fill() -> None:
    oms, books = _oms(_book(asks=[("0.96", "100")]))
    result = await oms.submit(_order(order_type="GTC"), _state())
    assert result.ack.status == "REJECTED"
    assert result.reason == "unsupported_paper_order_type"
    assert books.calls == 0


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        PaperOMSConfig(latency_ms=-1)
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        PaperOMSConfig(competition_fraction=Decimal("1"))


async def test_book_token_and_timestamp_are_validated() -> None:
    wrong = OrderBook(token_id="down", ts=NOW, asks=[], bids=[])
    oms, _ = _oms(wrong)
    with pytest.raises(ValueError, match="book token"):
        await oms.submit(_order(), _state())


async def test_empty_client_id_fails_closed() -> None:
    oms, _ = _oms(_book(asks=[]))
    with pytest.raises(ValueError, match="client_id"):
        await oms.submit(_order(client_id=""), _state())
