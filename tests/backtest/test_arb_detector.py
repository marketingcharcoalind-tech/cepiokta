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
    return ReplayTick(
        ts,
        Decimal("100000"),
        _book("up", ts, up),
        _book("down", ts, down),
    )


def _config() -> ArbDetectorConfig:
    return ArbDetectorConfig(
        fee_model=ZeroFee(),
        slippage_buffer=Decimal("0"),
        min_lock_edge=Decimal("0.001"),
        min_depth=Decimal("5"),
        max_lock_size=Decimal("50"),
        max_sum_asks=Decimal("1"),
    )


def test_valid_state_lasts_until_next_invalid_state():
    ticks = [_tick(0, "0.48", "0.49"), _tick(100, "0.50", "0.50")]
    episodes, rejects, tick_count, unique_states, valid_states = detect_round_episodes(
        7, ticks, _config()
    )
    assert tick_count == 2
    assert unique_states == 2
    assert valid_states == 1
    assert len(episodes) == 1
    assert episodes[0].implied_duration_ms == 100
    assert episodes[0].observations == 1
    assert sum(rejects.values()) == 1


def test_contiguous_valid_states_use_next_invalid_as_end():
    ticks = [
        _tick(0, "0.48", "0.49"),
        _tick(100, "0.47", "0.49"),
        _tick(250, "0.50", "0.50"),
    ]
    episodes, _, _, _, valid_states = detect_round_episodes(7, ticks, _config())
    assert valid_states == 2
    assert episodes[0].implied_duration_ms == 250
    assert episodes[0].observations == 2
    assert episodes[0].best_net_edge == Decimal("0.04")


def test_lvcf_duplicate_state_is_not_counted_twice():
    first = _tick(0, "0.48", "0.49")
    duplicate = ReplayTick(
        _BASE + timedelta(milliseconds=50),
        Decimal("100001"),
        first.book_up,
        first.book_down,
    )
    invalid = _tick(200, "0.50", "0.50")
    episodes, _, tick_count, unique_states, valid_states = detect_round_episodes(
        7, [first, duplicate, invalid], _config()
    )
    assert tick_count == 3
    assert unique_states == 2
    assert valid_states == 1
    assert episodes[0].observations == 1
    assert episodes[0].implied_duration_ms == 200


def test_invalid_state_splits_episodes():
    ticks = [
        _tick(0, "0.48", "0.49"),
        _tick(100, "0.50", "0.50"),
        _tick(200, "0.47", "0.49"),
        _tick(300, "0.50", "0.50"),
    ]
    episodes, rejects, _, _, valid_states = detect_round_episodes(7, ticks, _config())
    assert valid_states == 2
    assert len(episodes) == 2
    assert sum(rejects.values()) == 2


def test_theoretical_pnl_is_explicit_upper_bound():
    episodes, _, _, _, _ = detect_round_episodes(7, [_tick(0, "0.45", "0.45")], _config())
    assert episodes[0].theoretical_pnl_upper_bound == Decimal("1.00")
