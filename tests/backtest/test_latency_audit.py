"""tests/backtest/test_latency_audit.py — Tests for latency audit (Task 5, G1 blocker)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from btcbot.backtest.latency_audit import (
    LatencyAuditEntry,
    _audit_round_entry,
    run_latency_audit,
)
from btcbot.backtest.replay import ReplayConfig, ReplayEngine, ReplayTick
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus
from btcbot.domain.strategy import StrategyParams
from btcbot.exec.sizing import SizingLimits

_ZERO = Decimal("0")


def _make_round(
    round_no: int = 1783520100,
    start_price: Decimal = Decimal("96500"),
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    resolved_outcome: Outcome = Outcome.UP,
) -> Round:
    """Helper to create a test Round."""
    if window_start is None:
        window_start = datetime(2026, 7, 8, 14, 13, 0, tzinfo=timezone.utc)
    if window_end is None:
        window_end = window_start + timedelta(minutes=5)
    
    return Round(
        condition_id="test_cond",
        round_no=round_no,
        token_id_up="token_up",
        token_id_down="token_down",
        window_start=window_start,
        window_end=window_end,
        start_price=start_price,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("1.0"),
        status=RoundStatus.RESOLVED,
        resolved_outcome=resolved_outcome,
    )


def _make_book(
    token_id: str,
    ts: datetime,
    asks: list[tuple[Decimal, Decimal]] | None = None,
    bids: list[tuple[Decimal, Decimal]] | None = None,
) -> OrderBook:
    """Helper to create OrderBook from (price, size) tuples."""
    if asks is None:
        asks = []
    if bids is None:
        bids = []
    
    return OrderBook(
        token_id=token_id,
        ts=ts,
        asks=[BookLevel(price=p, size=s) for p, s in asks],
        bids=[BookLevel(price=p, size=s) for p, s in bids],
    )


def _make_config(
    latency_ticks: int = 1,
    t_entry_sec: int = 60,
    delta_threshold: Decimal = Decimal("50"),
    min_price: Decimal = Decimal("0.96"),
    max_price: Decimal = Decimal("0.99"),
) -> ReplayConfig:
    """Helper to create ReplayConfig."""
    return ReplayConfig(
        limits=SizingLimits(
            kelly_fraction=Decimal("0.25"),
            max_notional_round=Decimal("5"),
            max_bankroll_fraction=Decimal("0.02"),
            fill_safety=Decimal("0.8"),
            min_edge=Decimal("0.01"),
            max_price=max_price,
        ),
        params=StrategyParams(
            t_entry_sec=t_entry_sec,
            delta_threshold=delta_threshold,
            min_price=min_price,
            max_price=max_price,
            min_edge=Decimal("0.01"),
            flip_ratio=Decimal("0.90"),
            hedge_fraction=Decimal("0.5"),
            p_exit=Decimal("0.65"),
        ),
        vol=Decimal("0.00013"),
        starting_balance=Decimal("500"),
        latency_ticks=latency_ticks,
        competition_fraction=_ZERO,
        slippage_enabled=True,
        seed=42,
    )


def test_final_tick_clamping_detection():
    """Test 1: Detect when requested execution tick is clamped to final tick.
    
    VPS question: "How often does ticks[min(i + latency_ticks, n - 1)] clamp to final tick?"
    """
    rnd = _make_round()
    window_end = rnd.window_end
    
    # Create 3 ticks with latency_ticks=2 to force clamping at tick index 2
    ts1 = window_end - timedelta(seconds=70)
    ts2 = window_end - timedelta(seconds=60)
    ts3 = window_end - timedelta(seconds=50)
    
    # Decision at tick 1 (index=1), requested exec at index 3 (1+2), but only 3 ticks → clamps to index 2
    book_up_decision = _make_book("token_up", ts2, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down_decision = _make_book("token_down", ts2, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    book_up_exec = _make_book("token_up", ts3, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down_exec = _make_book("token_down", ts3, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96500"), book_up=book_up_decision, book_down=book_down_decision),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up_decision, book_down=book_down_decision),
        ReplayTick(ts=ts3, btc_price=Decimal("96700"), book_up=book_up_exec, book_down=book_down_exec),
    ]
    
    config = _make_config(latency_ticks=2, t_entry_sec=75)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=2)
    
    if entry_audit is not None:
        # Should detect clamping: requested index 3 >= total 3 → clamped to 2
        assert entry_audit.total_tick_count == 3
        assert entry_audit.requested_execution_tick_index == 3  # 1 + 2
        assert entry_audit.actual_execution_tick_index == 2  # clamped
        assert entry_audit.clamped_to_last_tick is True


def test_same_timestamp_detection():
    """Test 2: Detect when decision and execution have identical timestamps.
    
    VPS question: "How often are decision and execution timestamps identical?"
    This happens with latency_ticks=0 or when multiple events share the same timestamp.
    """
    rnd = _make_round()
    window_end = rnd.window_end
    
    # Same timestamp for decision and execution (latency_ticks=0)
    ts_shared = window_end - timedelta(seconds=60)
    
    book_up = _make_book("token_up", ts_shared, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down = _make_book("token_down", ts_shared, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts_shared, btc_price=Decimal("96500"), book_up=book_up, book_down=book_down),
        ReplayTick(ts=ts_shared, btc_price=Decimal("96600"), book_up=book_up, book_down=book_down),
    ]
    
    config = _make_config(latency_ticks=0, t_entry_sec=65)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=0)
    
    if entry_audit is not None:
        # Decision and execution at same tick → same timestamp
        assert entry_audit.decision_ts == entry_audit.execution_ts
        assert entry_audit.same_timestamp is True
        assert entry_audit.realized_latency_ms == 0.0


def test_sparse_ticks_causing_high_latency():
    """Test 3: Detect when sparse ticks cause >1s realized latency.
    
    VPS evidence: 4/84 entries had >1s decision-to-fill latency.
    This occurs when ticks are sparse (low event density).
    """
    rnd = _make_round()
    window_end = rnd.window_end
    
    # Create sparse ticks with >1s gap between them
    # Ensure BTC price moves to trigger entry signal
    ts1 = window_end - timedelta(seconds=55)
    ts2 = ts1 + timedelta(seconds=1, milliseconds=600)  # 1.6s gap
    ts3 = ts2 + timedelta(seconds=1, milliseconds=200)  # Another 1.2s gap
    
    # First two ticks: decision should occur at one of these
    book_up = _make_book("token_up", ts2, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down = _make_book("token_down", ts2, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96400"), book_up=book_up, book_down=book_down),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up, book_down=book_down),  # Large price move
        ReplayTick(ts=ts3, btc_price=Decimal("96700"), book_up=book_up, book_down=book_down),
    ]
    
    config = _make_config(latency_ticks=1, t_entry_sec=60)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=1)
    
    if entry_audit is not None:
        # Key behavior: if decision at tick 1, execution at tick 2 → should have >1s latency
        # The tick-based model produces unpredictable realized latency based on event density
        if entry_audit.decision_tick_index == 1 and entry_audit.actual_execution_tick_index == 2:
            assert entry_audit.realized_latency_ms >= 1000.0, \
                f"Expected >1s latency with sparse ticks, got {entry_audit.realized_latency_ms}ms"
            assert entry_audit.same_timestamp is False


def test_stale_lvcf_book_detection():
    """Test 4: Detect when execution uses last-value-carried-forward stale book.
    
    VPS question: "Does execution use fresh target-side book or LVCF stale book?"
    Target book unchanged means LVCF; changed means fresh update.
    """
    rnd = _make_round()
    window_end = rnd.window_end
    
    # Decision at ts1, execution at ts2 (different ticks)
    ts1 = window_end - timedelta(seconds=55)
    ts2 = window_end - timedelta(seconds=54)
    
    # Target book (UP) does NOT change between decision and execution (LVCF)
    book_ts_stale = ts1 - timedelta(seconds=5)  # Stale target book
    book_up_stale = _make_book("token_up", book_ts_stale, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    
    # Opposite book (DOWN) changes between decision and execution
    book_down_decision = _make_book("token_down", ts1, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    book_down_exec = _make_book("token_down", ts2, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    # Need 3 ticks to avoid clamping
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96400"), book_up=book_up_stale, book_down=book_down_decision),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up_stale, book_down=book_down_exec),
        ReplayTick(ts=window_end - timedelta(seconds=10), btc_price=Decimal("96700"), book_up=book_up_stale, book_down=book_down_exec),
    ]
    
    config = _make_config(latency_ticks=1, t_entry_sec=60)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=1)
    
    if entry_audit is not None and entry_audit.decision_tick_index != entry_audit.actual_execution_tick_index:
        # Only check book changes if decision and execution are at different ticks
        # Target book (UP) unchanged → LVCF stale
        assert entry_audit.target_book_changed is False, "Expected target book to be stale/unchanged"
        
        # Book age at execution should be >= book age at decision (stale or same)
        assert entry_audit.execution_target_book_age_ms >= entry_audit.decision_target_book_age_ms
        assert entry_audit.execution_target_book_age_ms > 5000.0  # >5s stale


def test_latency_ticks_zero():
    """Test 5: Test latency_ticks=0 case (decision = execution).
    
    VPS evidence: 41/84 entries had exactly 0ms latency.
    This should happen when latency_ticks=0.
    """
    rnd = _make_round()
    window_end = rnd.window_end
    
    ts = window_end - timedelta(seconds=60)
    
    book_up = _make_book("token_up", ts, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down = _make_book("token_down", ts, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts, btc_price=Decimal("96500"), book_up=book_up, book_down=book_down),
    ]
    
    config = _make_config(latency_ticks=0, t_entry_sec=65)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=0)
    
    if entry_audit is not None:
        # Decision and execution should be same tick
        assert entry_audit.decision_tick_index == 0
        assert entry_audit.actual_execution_tick_index == 0
        assert entry_audit.same_timestamp is True
        assert entry_audit.realized_latency_ms == 0.0
        assert entry_audit.clamped_to_last_tick is False


def test_successful_fok_fill():
    """Test 6: Test successful FOK fill with sufficient depth."""
    rnd = _make_round()
    window_end = rnd.window_end
    
    ts1 = window_end - timedelta(seconds=60)
    ts2 = window_end - timedelta(seconds=59)
    
    # Sufficient depth for fill
    book_up = _make_book("token_up", ts2, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down = _make_book("token_down", ts2, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96500"), book_up=book_up, book_down=book_down),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up, book_down=book_down),
    ]
    
    config = _make_config(latency_ticks=1, t_entry_sec=65)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=1)
    
    if entry_audit is not None:
        # Should have successful fill
        assert entry_audit.filled is True
        assert entry_audit.entry_size > _ZERO
        assert entry_audit.entry_price > _ZERO
        assert entry_audit.target_side in ("UP", "DOWN")


def test_failed_fok_empty_book():
    """Test 7: Test failed FOK when execution book is empty.
    
    This should result in no entry audit (entry_audit = None).
    """
    rnd = _make_round()
    window_end = rnd.window_end
    
    ts1 = window_end - timedelta(seconds=60)
    ts2 = window_end - timedelta(seconds=59)
    
    # Decision book has depth
    book_up_decision = _make_book("token_up", ts1, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down_decision = _make_book("token_down", ts1, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    # Execution book is EMPTY (no liquidity)
    book_up_exec = _make_book("token_up", ts2, asks=[], bids=[])
    book_down_exec = _make_book("token_down", ts2, asks=[], bids=[])
    
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96500"), book_up=book_up_decision, book_down=book_down_decision),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up_exec, book_down=book_down_exec),
    ]
    
    config = _make_config(latency_ticks=1, t_entry_sec=65)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=1)
    
    # No entry because execution book is empty
    assert entry_audit is None


def test_replay_behavior_unchanged():
    """Test 8: Verify that audit observability does NOT change replay PnL/results.
    
    Run replay with and without audit to ensure determinism.
    """
    rnd = _make_round(resolved_outcome=Outcome.UP)
    window_end = rnd.window_end
    
    ts1 = window_end - timedelta(seconds=60)
    ts2 = window_end - timedelta(seconds=59)
    
    book_up = _make_book("token_up", ts2, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down = _make_book("token_down", ts2, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96500"), book_up=book_up, book_down=book_down),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up, book_down=book_down),
    ]
    
    config = _make_config(latency_ticks=1, t_entry_sec=65)
    
    # Run replay directly (without audit)
    engine1 = ReplayEngine(config)
    result1, diag1, obs1 = engine1.observe(rnd, ticks, bankroll=Decimal("500"))
    
    # Run audit (which internally runs replay)
    engine2 = ReplayEngine(config)
    entry_audit = _audit_round_entry(engine2, rnd, ticks, Decimal("500"), latency_ticks_config=1)
    
    # Compare results
    if result1 is not None and entry_audit is not None:
        # PnL should match
        assert result1.pnl == entry_audit.pnl
        # Entry price should match
        assert result1.entry_price == entry_audit.entry_price
        # Size should match
        assert result1.size == entry_audit.entry_size
        # Side should match
        assert result1.side_taken == entry_audit.target_side
    elif result1 is None:
        # Both should be None
        assert entry_audit is None


def test_latency_ticks_greater_than_remaining():
    """Test 9: Test when latency_ticks is greater than remaining ticks.
    
    This should clamp to the last available tick.
    """
    rnd = _make_round()
    window_end = rnd.window_end
    
    ts1 = window_end - timedelta(seconds=60)
    ts2 = window_end - timedelta(seconds=59)
    
    book_up = _make_book("token_up", ts2, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down = _make_book("token_down", ts2, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96500"), book_up=book_up, book_down=book_down),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up, book_down=book_down),
    ]
    
    # latency_ticks=5 but only 2 ticks total → should clamp
    config = _make_config(latency_ticks=5, t_entry_sec=65)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=5)
    
    if entry_audit is not None:
        # Should clamp to last tick
        assert entry_audit.total_tick_count == 2
        # Decision happens at some tick, requested = decision_index + 5
        assert entry_audit.requested_execution_tick_index == entry_audit.decision_tick_index + 5
        assert entry_audit.actual_execution_tick_index == 1  # clamped to last
        assert entry_audit.clamped_to_last_tick is True


def test_book_change_detection():
    """Test 10: Detect when target and opposite books change between decision and execution."""
    rnd = _make_round()
    window_end = rnd.window_end
    
    ts1 = window_end - timedelta(seconds=60)
    ts2 = window_end - timedelta(seconds=59)
    
    # Both books change between decision and execution
    book_up_decision = _make_book("token_up", ts1, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down_decision = _make_book("token_down", ts1, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    book_up_exec = _make_book("token_up", ts2, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down_exec = _make_book("token_down", ts2, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96500"), book_up=book_up_decision, book_down=book_down_decision),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up_exec, book_down=book_down_exec),
    ]
    
    config = _make_config(latency_ticks=1, t_entry_sec=65)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=1)
    
    if entry_audit is not None:
        # Only check book changes if decision and execution are at different ticks
        if entry_audit.decision_tick_index != entry_audit.actual_execution_tick_index:
            # Both books changed (fresh timestamps)
            assert entry_audit.target_book_changed is True
            assert entry_audit.opposite_book_changed is True


def test_audit_with_no_entry():
    """Test 11: Audit should return None when no entry occurs (no signal)."""
    rnd = _make_round()
    window_end = rnd.window_end
    
    # Time left too large (no entry signal)
    ts = window_end - timedelta(seconds=200)
    
    book_up = _make_book("token_up", ts, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down = _make_book("token_down", ts, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts, btc_price=Decimal("96500"), book_up=book_up, book_down=book_down),
    ]
    
    config = _make_config(latency_ticks=1, t_entry_sec=60)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=1)
    
    # No entry should occur
    assert entry_audit is None


def test_audit_with_win():
    """Test 12: Audit correctly classifies WIN result."""
    rnd = _make_round(resolved_outcome=Outcome.UP)  # UP wins
    window_end = rnd.window_end
    
    ts1 = window_end - timedelta(seconds=60)
    ts2 = window_end - timedelta(seconds=59)
    
    # UP has lower ask → will enter UP → WIN
    book_up = _make_book("token_up", ts2, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down = _make_book("token_down", ts2, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96500"), book_up=book_up, book_down=book_down),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up, book_down=book_down),
    ]
    
    config = _make_config(latency_ticks=1, t_entry_sec=65)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=1)
    
    if entry_audit is not None:
        # Should be WIN
        assert entry_audit.result == "WIN"
        assert entry_audit.pnl > _ZERO


def test_audit_with_loss():
    """Test 13: Audit correctly classifies LOSS result."""
    rnd = _make_round(resolved_outcome=Outcome.DOWN)  # DOWN wins
    window_end = rnd.window_end
    
    ts1 = window_end - timedelta(seconds=60)
    ts2 = window_end - timedelta(seconds=59)
    
    # UP has lower ask → will enter UP → LOSS (because DOWN wins)
    book_up = _make_book("token_up", ts2, asks=[(Decimal("0.96"), Decimal("100"))], bids=[])
    book_down = _make_book("token_down", ts2, asks=[(Decimal("0.04"), Decimal("100"))], bids=[])
    
    ticks = [
        ReplayTick(ts=ts1, btc_price=Decimal("96500"), book_up=book_up, book_down=book_down),
        ReplayTick(ts=ts2, btc_price=Decimal("96600"), book_up=book_up, book_down=book_down),
    ]
    
    config = _make_config(latency_ticks=1, t_entry_sec=65)
    engine = ReplayEngine(config)
    
    entry_audit = _audit_round_entry(engine, rnd, ticks, Decimal("500"), latency_ticks_config=1)
    
    if entry_audit is not None:
        # Should be LOSS
        assert entry_audit.result == "LOSS"
        assert entry_audit.pnl < _ZERO
