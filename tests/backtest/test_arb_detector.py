"""Tests for read-only pure-arbitrage episode replay."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.backtest.arb_detector import ArbDetectorConfig, detect_round_episodes
from btcbot.backtest.replay import ReplayTick
from btcbot.domain.fees import ZeroFee
from btcbot.domain.models import BookLevel, OrderBook

_BASE = datetime(2026, 7, 12, tzinfo=UTC)


def _book(token: str, ts: datetime, ask: str) -> OrderBook:
    return OrderBook(token, ts, [], [BookLevel(Decimal(ask), Decimal("10"))])


def _tick(offset_ms: int, up: str, down: str) -> ReplayTick:
    ts = _BASE + timedelta(milliseconds=offset_ms)
    return ReplayTick(ts, Decimal("100000"), _book("up", ts, up), _book("down", ts, down))


def _config() -> ArbDetectorConfig:
    return ArbDetectorConfig(
        fee_model=ZeroFee(),
        slippage_buffer=Decimal("0"),
        min_lock_edge=Decimal("0.001"),
        min_depth=Decimal("5"),
        max_lock_size=Decimal("50"),
        max_sum_asks=Decimal("1"),
    )


def test_contiguous_valid_ticks_form_one_episode():
    ticks = [_tick(0, "0.48", "0.49"), _tick(100, "0.47", "0.49")]
    episodes, rejects, tick_count, valid_ticks = detect_round_episodes(7, ticks, _config())
    assert tick_count == 2
    assert valid_ticks == 2
    assert len(episodes) == 1
    assert episodes[0].duration_ms == 100
    assert episodes[0].observations == 2
    assert episodes[0].best_net_edge == Decimal("0.04")
    assert sum(rejects.values()) == 0


def test_invalid_tick_splits_episodes():
    ticks = [
        _tick(0, "0.48", "0.49"),
        _tick(100, "0.50", "0.50"),
        _tick(200, "0.47", "0.49"),
    ]
    episodes, rejects, _, valid_ticks = detect_round_episodes(7, ticks, _config())
    assert valid_ticks == 2
    assert len(episodes) == 2
    assert sum(rejects.values()) == 1


def test_theoretical_pnl_is_capped_by_depth():
    episodes, _, _, _ = detect_round_episodes(
        7, [_tick(0, "0.45", "0.45")], _config()
    )
    assert episodes[0].theoretical_pnl == Decimal("1.00")
