"""Non-vacuous integration tests for ReplayEngine time-based latency."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

from btcbot.backtest import replay as replay_module
from btcbot.backtest.replay import (
    ROUND_FILLED,
    ROUND_SIGNAL_NO_FILL,
    ReplayConfig,
    ReplayEngine,
    ReplayTick,
)
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus
from btcbot.domain.strategy import EnterOrder, Exit, Hedge, NoOp, StrategyParams
from btcbot.exec.sizing import SizingLimits

D = Decimal
UTC = timezone.utc
BASE = datetime(2026, 7, 8, 14, 0, 0, tzinfo=UTC)
WINDOW_END = BASE + timedelta(minutes=5)
UP = "token_up"
DOWN = "token_down"


def _book(
    token: str,
    ts: datetime,
    *,
    ask: str | None,
    bid: str | None = "0.40",
    depth: str = "100",
) -> OrderBook:
    return OrderBook(
        token_id=token,
        ts=ts,
        bids=[] if bid is None else [BookLevel(D(bid), D(depth))],
        asks=[] if ask is None else [BookLevel(D(ask), D(depth))],
    )


def _tick(
    offset_ms: int,
    *,
    btc: str = "96600",
    up_ask: str | None = "0.60",
    down_ask: str | None = "0.20",
    up_bid: str | None = "0.55",
    down_bid: str | None = "0.15",
) -> ReplayTick:
    ts = WINDOW_END - timedelta(seconds=60) + timedelta(milliseconds=offset_ms)
    return ReplayTick(
        ts=ts,
        btc_price=D(btc),
        book_up=_book(UP, ts, ask=up_ask, bid=up_bid),
        book_down=_book(DOWN, ts, ask=down_ask, bid=down_bid),
    )


def _round(outcome: Outcome = Outcome.UP) -> Round:
    return Round(
        condition_id="condition",
        round_no=1783520100,
        token_id_up=UP,
        token_id_down=DOWN,
        window_start=BASE,
        window_end=WINDOW_END,
        start_price=D("96500"),
        tick_size=D("0.01"),
        min_order_size=D("1"),
        status=RoundStatus.RESOLVED,
        resolved_outcome=outcome,
    )


def _config(
    *,
    mode: str | None = "time",
    latency_ms: int = 100,
    latency_ticks: int = 1,
) -> ReplayConfig:
    kwargs: dict[str, object] = {}
    if mode is not None:
        kwargs.update(
            latency_mode=mode,
            latency_ticks=latency_ticks,
            latency_ms=latency_ms,
        )
    return ReplayConfig(
        limits=SizingLimits(
            kelly_fraction=D("0.25"),
            max_notional_round=D("100"),
            max_bankroll_fraction=D("0.5"),
            fill_safety=D("0.8"),
            min_edge=D("0.001"),
            max_price=D("0.99"),
            min_order_size=D("1"),
            tick_size=D("0.01"),
        ),
        params=StrategyParams(
            t_entry_sec=120,
            delta_threshold=D("1"),
            min_price=D("0.10"),
            max_price=D("0.99"),
            min_edge=D("0.001"),
            flip_ratio=D("0.90"),
            hedge_fraction=D("0.5"),
            p_exit=D("0.30"),
        ),
        vol=D("5"),
        starting_balance=D("1000"),
        **kwargs,
    )


def _entry(price: str = "0.60") -> EnterOrder:
    return EnterOrder(token_id=UP, outcome="UP", price=D(price))


def _hedge(price: str = "0.20") -> Hedge:
    return Hedge(
        token_id=DOWN,
        outcome="DOWN",
        price=D(price),
        hedge_fraction=D("0.5"),
    )


def _exit(price: str = "0.55") -> Exit:
    return Exit(token_id=UP, outcome="UP", price=D(price))


def test_tick_mode_complete_regression_default_vs_explicit() -> None:
    ticks = [_tick(0), _tick(1000, up_ask="0.61")]
    default_engine = ReplayEngine(_config(mode=None))
    explicit_engine = ReplayEngine(_config(mode="ticks", latency_ticks=1))

    default_engine._strategy.on_tick = Mock(return_value=[_entry()])
    explicit_engine._strategy.on_tick = Mock(return_value=[_entry()])

    old_result, old_diag, old_obs = default_engine.observe(
        _round(), ticks, bankroll=D("1000")
    )
    new_result, new_diag, new_obs = explicit_engine.observe(
        _round(), ticks, bankroll=D("1000")
    )

    assert old_result is not None
    assert new_result is not None
    assert old_result == new_result
    assert old_diag == new_diag
    assert old_obs.classification == new_obs.classification
    assert old_obs.fills == new_obs.fills
    assert old_obs.entry_decision_tick_index == new_obs.entry_decision_tick_index
    assert old_obs.actual_entry_execution_tick_index == new_obs.actual_entry_execution_tick_index
    assert old_obs.entry_fill_ts == new_obs.entry_fill_ts


def test_time_mode_exact_50ms_entry() -> None:
    ticks = [_tick(0), _tick(50), _tick(100)]
    engine = ReplayEngine(_config(latency_ms=50))
    engine._strategy.on_tick = Mock(side_effect=[[_entry()], [NoOp()], [NoOp()]])

    result, _diag, obs = engine.observe(_round(), ticks, bankroll=D("1000"))

    assert result is not None
    assert obs.classification == ROUND_FILLED
    assert obs.entry_decision_tick_index == 0
    assert obs.actual_entry_execution_tick_index == 1
    assert obs.entry_decision_ts == ticks[0].ts
    assert obs.entry_fill_ts == ticks[1].ts
    assert obs.requested_entry_execution_ts == ticks[1].ts
    assert obs.realized_entry_latency_ms == 50.0
    assert obs.entry_execution_overshoot_ms == 0.0
    assert obs.successful_entry_limit_price == D("0.60")


def test_time_mode_100ms_request_executes_at_130ms() -> None:
    ticks = [_tick(0), _tick(50), _tick(130), _tick(200)]
    engine = ReplayEngine(_config(latency_ms=100))
    engine._strategy.on_tick = Mock(
        side_effect=[[_entry()], [NoOp()], [NoOp()], [NoOp()]]
    )

    result, _diag, obs = engine.observe(_round(), ticks, bankroll=D("1000"))

    assert result is not None
    assert obs.classification == ROUND_FILLED
    assert obs.entry_decision_tick_index == 0
    assert obs.actual_entry_execution_tick_index == 2
    assert obs.requested_entry_execution_ts == ticks[0].ts + timedelta(milliseconds=100)
    assert obs.entry_fill_ts == ticks[2].ts
    assert obs.realized_entry_latency_ms == 130.0
    assert obs.entry_execution_overshoot_ms == 30.0


def test_time_mode_no_future_entry_fails_closed() -> None:
    ticks = [_tick(0)]
    engine = ReplayEngine(_config(latency_ms=100))
    engine._strategy.on_tick = Mock(return_value=[_entry()])

    result, _diag, obs = engine.observe(_round(), ticks, bankroll=D("1000"))

    assert result is None
    assert obs.classification == ROUND_SIGNAL_NO_FILL
    assert obs.no_future_tick_entry_attempts == 1
    assert obs.fills == 0
    assert obs.entry_fill_ts is None
    assert obs.actual_entry_execution_tick_index is None


def test_failed_fok_then_later_success_uses_successful_attempt_observability() -> None:
    ticks = [
        _tick(0),
        _tick(100, up_ask="0.70"),
        _tick(200, up_ask="0.60"),
        _tick(300, up_ask="0.60"),
    ]
    engine = ReplayEngine(_config(latency_ms=100))
    engine._strategy.on_tick = Mock(
        side_effect=[[_entry("0.60")], [_entry("0.60")], [NoOp()], [NoOp()]]
    )

    result, _diag, obs = engine.observe(_round(), ticks, bankroll=D("1000"))

    assert result is not None
    assert obs.classification == ROUND_FILLED
    assert obs.entry_decision_tick_index == 1
    assert obs.actual_entry_execution_tick_index == 2
    assert obs.entry_decision_ts == ticks[1].ts
    assert obs.entry_fill_ts == ticks[2].ts
    assert obs.successful_entry_limit_price == D("0.60")


def test_hedge_uses_time_selector_and_future_tick() -> None:
    ticks = [_tick(0), _tick(50), _tick(100), _tick(150)]
    engine = ReplayEngine(_config(latency_ms=50))
    engine._strategy.on_tick = Mock(
        side_effect=[[_entry()], [_hedge()], [NoOp()], [NoOp()]]
    )

    result, _diag, obs = engine.observe(_round(), ticks, bankroll=D("1000"))

    assert result is not None
    assert obs.classification == ROUND_FILLED
    assert result.hedge_cost > D("0")
    assert obs.no_future_tick_hedge_attempts == 0


def test_hedge_no_future_tick_increments_counter_without_hedge_fill() -> None:
    ticks = [_tick(0), _tick(50)]
    engine = ReplayEngine(_config(latency_ms=50))
    engine._strategy.on_tick = Mock(side_effect=[[_entry()], [_hedge()]])

    result, _diag, obs = engine.observe(_round(), ticks, bankroll=D("1000"))

    assert result is not None
    assert obs.classification == ROUND_FILLED
    assert obs.no_future_tick_hedge_attempts == 1
    assert result.hedge_cost == D("0")


def test_exit_uses_time_selector_and_closes_position() -> None:
    ticks = [_tick(0), _tick(50), _tick(100), _tick(150)]
    engine = ReplayEngine(_config(latency_ms=50))
    engine._strategy.on_tick = Mock(
        side_effect=[[_entry()], [_exit()], [NoOp()], [NoOp()]]
    )

    result, _diag, obs = engine.observe(_round(), ticks, bankroll=D("1000"))

    assert result is not None
    assert obs.classification == ROUND_FILLED
    assert obs.no_future_tick_exit_attempts == 0
    assert result.settled == D("0")


def test_exit_no_future_tick_increments_counter_and_position_settles() -> None:
    ticks = [_tick(0), _tick(50)]
    engine = ReplayEngine(_config(latency_ms=50))
    engine._strategy.on_tick = Mock(side_effect=[[_entry()], [_exit()]])

    result, _diag, obs = engine.observe(_round(), ticks, bankroll=D("1000"))

    assert result is not None
    assert obs.classification == ROUND_FILLED
    assert obs.no_future_tick_exit_attempts == 1
    assert result.settled > D("0")


def test_replay_summary_aggregates_exact_no_future_entry_count() -> None:
    ticks = [_tick(0)]
    engine = ReplayEngine(_config(latency_ms=100))
    engine._strategy.on_tick = Mock(return_value=[_entry()])

    summary = engine.run([(_round(), ticks), (_round(Outcome.DOWN), ticks)])

    assert summary.rounds_entered == 0
    assert summary.no_future_tick_entry_attempts == 2
    assert summary.no_future_tick_hedge_attempts == 0
    assert summary.no_future_tick_exit_attempts == 0


def test_timestamp_tuple_is_built_once_and_reused_for_multiple_decisions() -> None:
    ticks = [_tick(i * 50) for i in range(8)]
    engine = ReplayEngine(_config(latency_ms=50))

    def decisions(_signal, _book, position):
        return [_entry()] if position is None else [_hedge()]

    engine._strategy.on_tick = Mock(side_effect=decisions)
    original = replay_module._select_execution_tick_fast
    captured: list[tuple[datetime, ...]] = []

    def instrumented(ticks_arg, timestamps_arg, *args, **kwargs):
        captured.append(timestamps_arg)
        return original(ticks_arg, timestamps_arg, *args, **kwargs)

    with patch.object(replay_module, "_select_execution_tick_fast", side_effect=instrumented):
        result, _diag, _obs = engine.observe(_round(), ticks, bankroll=D("1000"))

    assert result is not None
    assert len(captured) >= 3
    first = captured[0]
    assert len(first) == len(ticks)
    assert all(item is first for item in captured)
