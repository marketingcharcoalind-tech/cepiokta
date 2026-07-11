"""tests/backtest/test_execution_selection.py — Tests for execution tick selector (Sub-Task 1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from btcbot.backtest.replay import ExecutionSelection, ReplayTick, select_execution_tick
from btcbot.domain.models import BookLevel, OrderBook

_ZERO = Decimal("0")


def _make_book(token_id: str, ts: datetime) -> OrderBook:
    """Helper to create minimal OrderBook."""
    return OrderBook(
        token_id=token_id,
        ts=ts,
        asks=[BookLevel(price=Decimal("0.5"), size=Decimal("100"))],
        bids=[],
    )


def _make_ticks(base_ts: datetime, count: int, interval_ms: int = 1000) -> list[ReplayTick]:
    """Helper to create sequence of ticks with regular intervals."""
    ticks = []
    for i in range(count):
        ts = base_ts + timedelta(milliseconds=i * interval_ms)
        ticks.append(
            ReplayTick(
                ts=ts,
                btc_price=Decimal("96500"),
                book_up=_make_book("token_up", ts),
                book_down=_make_book("token_down", ts),
            )
        )
    return ticks


def test_tick_mode_latency_0():
    """Test tick mode with latency_ticks=0 (decision = execution)."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=5)
    
    result = select_execution_tick(
        ticks,
        decision_index=2,
        latency_mode="ticks",
        latency_ticks=0,
        latency_ms=100,  # ignored in tick mode
    )
    
    assert result.latency_mode == "ticks"
    assert result.decision_tick_index == 2
    assert result.requested_execution_tick_index == 2  # 2 + 0
    assert result.actual_execution_tick_index == 2
    assert result.tick_clamped is False
    assert result.no_future_tick is False
    assert result.configured_latency_ticks == 0
    assert result.configured_latency_ms is None
    assert result.realized_latency_ms == 0.0  # same tick
    assert result.execution_overshoot_ms is None


def test_tick_mode_latency_1():
    """Test tick mode with latency_ticks=1 (historical default)."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=5)
    
    result = select_execution_tick(
        ticks,
        decision_index=2,
        latency_mode="ticks",
        latency_ticks=1,
        latency_ms=100,
    )
    
    assert result.latency_mode == "ticks"
    assert result.decision_tick_index == 2
    assert result.requested_execution_tick_index == 3  # 2 + 1
    assert result.actual_execution_tick_index == 3
    assert result.tick_clamped is False
    assert result.configured_latency_ticks == 1
    assert result.realized_latency_ms == 1000.0  # 1 second gap


def test_tick_mode_final_tick_clamp():
    """Test tick mode clamps to final tick when requested >= n."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=3)
    
    result = select_execution_tick(
        ticks,
        decision_index=2,  # final tick
        latency_mode="ticks",
        latency_ticks=1,
        latency_ms=100,
    )
    
    assert result.requested_execution_tick_index == 3  # 2 + 1
    assert result.actual_execution_tick_index == 2  # clamped to final (n-1)
    assert result.tick_clamped is True
    assert result.realized_latency_ms == 0.0  # clamped to same tick


def test_tick_mode_latency_greater_than_remaining():
    """Test tick mode with large latency_ticks."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=3)
    
    result = select_execution_tick(
        ticks,
        decision_index=0,
        latency_mode="ticks",
        latency_ticks=5,
        latency_ms=100,
    )
    
    assert result.requested_execution_tick_index == 5  # 0 + 5
    assert result.actual_execution_tick_index == 2  # clamped to final
    assert result.tick_clamped is True


def test_time_mode_0ms():
    """Test time mode with 0ms latency (select first tick at or after decision_ts)."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=5)
    
    result = select_execution_tick(
        ticks,
        decision_index=2,
        latency_mode="time",
        latency_ticks=1,  # ignored in time mode
        latency_ms=0,
    )
    
    assert result.latency_mode == "time"
    assert result.decision_tick_index == 2
    assert result.requested_execution_tick_index is None
    assert result.requested_execution_ts == ticks[2].ts
    assert result.actual_execution_tick_index == 2  # same tick at or after
    assert result.no_future_tick is False
    assert result.configured_latency_ms == 0
    assert result.realized_latency_ms == 0.0
    assert result.execution_overshoot_ms == 0.0


def test_time_mode_50ms_exact_target():
    """Test time mode with exact tick at target time."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    # Create ticks at 0ms, 50ms, 100ms, 150ms
    ticks = _make_ticks(base_ts, count=4, interval_ms=50)
    
    result = select_execution_tick(
        ticks,
        decision_index=0,
        latency_mode="time",
        latency_ticks=1,
        latency_ms=50,
    )
    
    assert result.latency_mode == "time"
    assert result.requested_execution_ts == base_ts + timedelta(milliseconds=50)
    assert result.actual_execution_tick_index == 1  # exact match at 50ms
    assert result.actual_execution_ts == ticks[1].ts
    assert result.realized_latency_ms == 50.0
    assert result.execution_overshoot_ms == 0.0  # exact target


def test_time_mode_100ms_with_overshoot():
    """Test time mode with next tick at 130ms (realized 130ms, overshoot 30ms)."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    # Create ticks at 0ms, 50ms, 130ms, 200ms
    ticks = [
        ReplayTick(
            ts=base_ts,
            btc_price=Decimal("96500"),
            book_up=_make_book("token_up", base_ts),
            book_down=_make_book("token_down", base_ts),
        ),
        ReplayTick(
            ts=base_ts + timedelta(milliseconds=50),
            btc_price=Decimal("96500"),
            book_up=_make_book("token_up", base_ts + timedelta(milliseconds=50)),
            book_down=_make_book("token_down", base_ts + timedelta(milliseconds=50)),
        ),
        ReplayTick(
            ts=base_ts + timedelta(milliseconds=130),
            btc_price=Decimal("96500"),
            book_up=_make_book("token_up", base_ts + timedelta(milliseconds=130)),
            book_down=_make_book("token_down", base_ts + timedelta(milliseconds=130)),
        ),
    ]
    
    result = select_execution_tick(
        ticks,
        decision_index=0,
        latency_mode="time",
        latency_ticks=1,
        latency_ms=100,
    )
    
    assert result.requested_execution_ts == base_ts + timedelta(milliseconds=100)
    assert result.actual_execution_tick_index == 2  # first tick at or after 100ms
    assert result.realized_latency_ms == 130.0
    assert result.execution_overshoot_ms == 30.0  # 130 - 100


def test_time_mode_duplicate_timestamps():
    """Test time mode handles duplicate timestamps correctly."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    # Two ticks at same timestamp
    ticks = [
        ReplayTick(
            ts=base_ts,
            btc_price=Decimal("96500"),
            book_up=_make_book("token_up", base_ts),
            book_down=_make_book("token_down", base_ts),
        ),
        ReplayTick(
            ts=base_ts,  # duplicate timestamp
            btc_price=Decimal("96600"),
            book_up=_make_book("token_up", base_ts),
            book_down=_make_book("token_down", base_ts),
        ),
        ReplayTick(
            ts=base_ts + timedelta(milliseconds=100),
            btc_price=Decimal("96700"),
            book_up=_make_book("token_up", base_ts + timedelta(milliseconds=100)),
            book_down=_make_book("token_down", base_ts + timedelta(milliseconds=100)),
        ),
    ]
    
    # Decision at first tick (index 0), 50ms latency should select first tick at or after 50ms
    result = select_execution_tick(
        ticks,
        decision_index=0,
        latency_mode="time",
        latency_ticks=1,
        latency_ms=50,
    )
    
    assert result.actual_execution_tick_index == 2  # first tick >= 50ms
    assert result.realized_latency_ms == 100.0


def test_time_mode_sparse_events():
    """Test time mode with sparse events (large gaps)."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    # Ticks at 0ms, 2000ms (2s gap)
    ticks = _make_ticks(base_ts, count=2, interval_ms=2000)
    
    result = select_execution_tick(
        ticks,
        decision_index=0,
        latency_mode="time",
        latency_ticks=1,
        latency_ms=100,
    )
    
    assert result.actual_execution_tick_index == 1
    assert result.realized_latency_ms == 2000.0
    assert result.execution_overshoot_ms == 1900.0  # 2000 - 100


def test_time_mode_no_future_tick():
    """Test time mode when no tick exists at or after requested_ts."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=3, interval_ms=100)
    
    # Decision at final tick with 100ms latency → no future tick
    result = select_execution_tick(
        ticks,
        decision_index=2,
        latency_mode="time",
        latency_ticks=1,
        latency_ms=100,
    )
    
    assert result.latency_mode == "time"
    assert result.no_future_tick is True
    assert result.actual_execution_tick_index is None
    assert result.actual_execution_ts is None
    assert result.realized_latency_ms is None
    assert result.execution_overshoot_ms is None


def test_time_mode_decision_on_final_tick():
    """Test time mode with decision on final tick."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=5)
    
    result = select_execution_tick(
        ticks,
        decision_index=4,  # final tick
        latency_mode="time",
        latency_ticks=1,
        latency_ms=50,
    )
    
    assert result.no_future_tick is True
    assert result.actual_execution_tick_index is None


def test_invalid_mode():
    """Test that invalid latency_mode raises ValueError."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=3)
    
    with pytest.raises(ValueError, match="latency_mode must be 'ticks' or 'time'"):
        select_execution_tick(
            ticks,
            decision_index=0,
            latency_mode="invalid",
            latency_ticks=1,
            latency_ms=100,
        )


def test_negative_tick_latency():
    """Test that negative latency_ticks raises ValueError."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=3)
    
    with pytest.raises(ValueError, match="latency_ticks must be >= 0"):
        select_execution_tick(
            ticks,
            decision_index=0,
            latency_mode="ticks",
            latency_ticks=-1,
            latency_ms=100,
        )


def test_negative_time_latency():
    """Test that negative latency_ms raises ValueError."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=3)
    
    with pytest.raises(ValueError, match="latency_ms must be >= 0"):
        select_execution_tick(
            ticks,
            decision_index=0,
            latency_mode="time",
            latency_ticks=1,
            latency_ms=-100,
        )


def test_invalid_decision_index():
    """Test that invalid decision_index raises ValueError."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = _make_ticks(base_ts, count=3)
    
    with pytest.raises(ValueError, match="decision_index .* out of bounds"):
        select_execution_tick(
            ticks,
            decision_index=5,  # >= len(ticks)
            latency_mode="ticks",
            latency_ticks=1,
            latency_ms=100,
        )
    
    with pytest.raises(ValueError, match="decision_index .* out of bounds"):
        select_execution_tick(
            ticks,
            decision_index=-1,
            latency_mode="ticks",
            latency_ticks=1,
            latency_ms=100,
        )


def test_empty_ticks():
    """Test that empty ticks raises ValueError."""
    with pytest.raises(ValueError, match="ticks must be non-empty"):
        select_execution_tick(
            [],
            decision_index=0,
            latency_mode="ticks",
            latency_ticks=1,
            latency_ms=100,
        )


def test_naive_datetime():
    """Test that naive datetime raises ValueError."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0)  # No timezone
    ticks = [
        ReplayTick(
            ts=base_ts,
            btc_price=Decimal("96500"),
            book_up=_make_book("token_up", base_ts),
            book_down=_make_book("token_down", base_ts),
        )
    ]
    
    with pytest.raises(ValueError, match="naive timestamp"):
        select_execution_tick(
            ticks,
            decision_index=0,
            latency_mode="ticks",
            latency_ticks=1,
            latency_ms=100,
        )


def test_unsorted_timestamps():
    """Test that unsorted timestamps raises ValueError."""
    base_ts = datetime(2026, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
    ticks = [
        ReplayTick(
            ts=base_ts + timedelta(seconds=2),
            btc_price=Decimal("96500"),
            book_up=_make_book("token_up", base_ts),
            book_down=_make_book("token_down", base_ts),
        ),
        ReplayTick(
            ts=base_ts,  # Earlier than previous
            btc_price=Decimal("96500"),
            book_up=_make_book("token_up", base_ts),
            book_down=_make_book("token_down", base_ts),
        ),
    ]
    
    with pytest.raises(ValueError, match="ticks not sorted"):
        select_execution_tick(
            ticks,
            decision_index=0,
            latency_mode="ticks",
            latency_ticks=1,
            latency_ms=100,
        )



def test_settings_latency_mode_validation():
    """Test Settings validates latency_mode."""
    from btcbot.config.settings import Settings
    
    # Valid modes
    s1 = Settings(backtest_latency_mode="ticks")
    assert s1.backtest_latency_mode == "ticks"
    
    s2 = Settings(backtest_latency_mode="time")
    assert s2.backtest_latency_mode == "time"
    
    # Invalid mode
    with pytest.raises(ValueError, match="latency_mode harus 'ticks' atau 'time'"):
        Settings(backtest_latency_mode="invalid")


def test_settings_latency_ticks_non_negative():
    """Test Settings validates latency_ticks >= 0."""
    from btcbot.config.settings import Settings
    
    s = Settings(backtest_latency_ticks=0)
    assert s.backtest_latency_ticks == 0
    
    s = Settings(backtest_latency_ticks=5)
    assert s.backtest_latency_ticks == 5
    
    with pytest.raises(ValueError, match="tidak boleh negatif"):
        Settings(backtest_latency_ticks=-1)


def test_settings_latency_ms_non_negative():
    """Test Settings validates latency_ms >= 0."""
    from btcbot.config.settings import Settings
    
    s = Settings(backtest_latency_ms=0)
    assert s.backtest_latency_ms == 0
    
    s = Settings(backtest_latency_ms=500)
    assert s.backtest_latency_ms == 500
    
    with pytest.raises(ValueError, match="tidak boleh negatif"):
        Settings(backtest_latency_ms=-100)


def test_replay_config_from_settings_maps_latency_fields():
    """Test ReplayConfig.from_settings() correctly maps latency fields."""
    from btcbot.backtest.replay import ReplayConfig
    from btcbot.config.settings import Settings
    
    settings = Settings(
        backtest_latency_mode="time",
        backtest_latency_ticks=2,
        backtest_latency_ms=250,
    )
    
    config = ReplayConfig.from_settings(settings, delta_threshold=Decimal("50"))
    
    assert config.latency_mode == "time"
    assert config.latency_ticks == 2
    assert config.latency_ms == 250


def test_replay_config_default_backward_compatible():
    """Test ReplayConfig defaults remain backward compatible with tick mode."""
    from btcbot.backtest.replay import ReplayConfig
    from btcbot.domain.fees import ProportionalTakerFee
    from btcbot.domain.strategy import StrategyParams
    from btcbot.exec.sizing import SizingLimits
    
    config = ReplayConfig(
        limits=SizingLimits(
            kelly_fraction=Decimal("0.25"),
            max_notional_round=Decimal("5"),
            max_bankroll_fraction=Decimal("0.02"),
            fill_safety=Decimal("0.8"),
            min_edge=Decimal("0.01"),
            max_price=Decimal("0.99"),
        ),
        params=StrategyParams(
            t_entry_sec=60,
            delta_threshold=Decimal("50"),
            min_price=Decimal("0.80"),
            max_price=Decimal("0.99"),
            min_edge=Decimal("0.01"),
            flip_ratio=Decimal("0.90"),
            hedge_fraction=Decimal("0.5"),
            p_exit=Decimal("0.65"),
        ),
        vol=Decimal("1"),
        starting_balance=Decimal("500"),
    )
    
    # Default should be tick mode with latency_ticks=1
    assert config.latency_mode == "ticks"
    assert config.latency_ticks == 1
    assert config.latency_ms == 100
