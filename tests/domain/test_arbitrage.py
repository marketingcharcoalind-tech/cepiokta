"""Unit tests for pure lock-pair detection."""

from datetime import UTC, datetime
from decimal import Decimal

from btcbot.domain.arbitrage import (
    REJECT_DEPTH_LOW,
    REJECT_EDGE_LOW,
    REJECT_EMPTY_BOOK,
    detect_lock_pair,
)
from btcbot.domain.fees import ZeroFee
from btcbot.domain.models import BookLevel, OrderBook

_TS = datetime(2026, 7, 12, tzinfo=UTC)


def _book(token: str, ask: str | None, depth: str = "10") -> OrderBook:
    asks = [] if ask is None else [BookLevel(Decimal(ask), Decimal(depth))]
    return OrderBook(token, _TS, [], asks)


def _detect(up: OrderBook, down: OrderBook, **overrides):
    params = {
        "round_no": 1,
        "book_up": up,
        "book_down": down,
        "fee_model": ZeroFee(),
        "slippage_buffer": Decimal("0"),
        "min_lock_edge": Decimal("0.001"),
        "min_depth": Decimal("5"),
        "max_sum_asks": Decimal("1"),
    }
    params.update(overrides)
    return detect_lock_pair(**params)


def test_valid_lock_pair_math():
    result = _detect(_book("up", "0.48"), _book("down", "0.49"))
    assert result.valid is True
    assert result.sum_asks == Decimal("0.97")
    assert result.net_lock_edge == Decimal("0.03")
    assert result.max_lock_size == Decimal("10")


def test_empty_book_rejected():
    result = _detect(_book("up", None), _book("down", "0.49"))
    assert result.valid is False
    assert result.reject_reason == REJECT_EMPTY_BOOK


def test_edge_and_depth_rejections():
    edge = _detect(
        _book("up", "0.50"),
        _book("down", "0.49"),
        min_lock_edge=Decimal("0.02"),
    )
    assert edge.reject_reason == REJECT_EDGE_LOW
    depth = _detect(_book("up", "0.45", "2"), _book("down", "0.45", "3"))
    assert depth.reject_reason == REJECT_DEPTH_LOW


def test_fee_and_slippage_reduce_edge():
    result = _detect(
        _book("up", "0.48"),
        _book("down", "0.49"),
        slippage_buffer=Decimal("0.01"),
    )
    assert result.net_lock_edge == Decimal("0.02")
