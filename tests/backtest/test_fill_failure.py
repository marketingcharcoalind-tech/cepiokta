"""Task A — fill-failure observability (NO_SIGNAL vs SIGNAL_NO_FILL vs FILLED).

Membuktikan report dapat membedakan "tak ada edge" (NO_SIGNAL) dari "edge ada
tapi tak executable karena likuiditas ekor" (SIGNAL_NO_FILL). Observability MURNI:
tidak mengubah PnL/keputusan (regresi diuji terpisah di test_replay).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.backtest.replay import (
    ROUND_FILLED,
    ROUND_NO_SIGNAL,
    ROUND_SIGNAL_NO_FILL,
    ReplayConfig,
    ReplayEngine,
    ReplayTick,
    RunAccumulator,
)
from btcbot.backtest.report import build_report, format_report
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus
from btcbot.domain.strategy import StrategyParams
from btcbot.exec.sizing import SizingLimits

WINDOW_END = datetime(2026, 6, 26, 13, 20, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)
UP = "up-tok"
DOWN = "down-tok"


def _ob(token: str, *, asks: list[tuple[str, str]], bids: list[tuple[str, str]]) -> OrderBook:
    return OrderBook(
        token_id=token,
        ts=WINDOW_START,
        bids=[BookLevel(Decimal(p), Decimal(s)) for p, s in bids],
        asks=[BookLevel(Decimal(p), Decimal(s)) for p, s in asks],
    )


def _round(i: int = 0) -> Round:
    return Round(
        condition_id=f"0xc{i}",
        round_no=1782480000 + i * 300,
        token_id_up=UP,
        token_id_down=DOWN,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        start_price=Decimal("65000"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("1"),
        status=RoundStatus.RESOLVED,
        resolved_outcome=Outcome.UP,
    )


def _config(*, latency: int = 1) -> ReplayConfig:
    return ReplayConfig(
        limits=SizingLimits(
            kelly_fraction=Decimal("0.25"),
            max_notional_round=Decimal("5"),
            max_bankroll_fraction=Decimal("0.02"),
            fill_safety=Decimal("0.8"),
            min_edge=Decimal("0.01"),
            max_price=Decimal("0.99"),
        ),
        params=StrategyParams(
            t_entry_sec=20,
            delta_threshold=Decimal("1"),
            min_price=Decimal("0.80"),
            max_price=Decimal("0.99"),
            min_edge=Decimal("0.01"),
            flip_ratio=Decimal("0.90"),
            hedge_fraction=Decimal("0.5"),
            p_exit=Decimal("0.65"),
        ),
        vol=Decimal("1"),
        starting_balance=Decimal("200"),
        latency_ticks=latency,
        seed=42,
    )


def _up_tick(ts: datetime, *, up_asks: list[tuple[str, str]]) -> ReplayTick:
    """Tick dengan UP memimpin kuat (delta besar); ASK UP = ``up_asks``."""
    return ReplayTick(
        ts=ts,
        btc_price=Decimal("65120"),
        book_up=_ob(UP, asks=up_asks, bids=[("0.88", "100")]),
        book_down=_ob(DOWN, asks=[("0.10", "100")], bids=[("0.08", "100")]),
    )


def _filled_ticks() -> list[ReplayTick]:
    """Decision book penuh + exec book penuh → FILLED."""
    full = [("0.90", "100")]
    return [
        _up_tick(WINDOW_END - timedelta(seconds=12), up_asks=full),
        _up_tick(WINDOW_END - timedelta(seconds=10), up_asks=full),
    ]


def _signal_no_fill_ticks() -> list[ReplayTick]:
    """Decision book PENUH (EnterOrder keluar) tapi exec book KOSONG → FOK gagal."""
    return [
        _up_tick(WINDOW_END - timedelta(seconds=12), up_asks=[("0.90", "100")]),
        _up_tick(WINDOW_END - timedelta(seconds=10), up_asks=[]),  # exec: ASK UP kosong
    ]


def _flat_tick() -> ReplayTick:
    """Δ=0 → p_win=0.5 → net_edge<0 → tak ada EnterOrder (NO_SIGNAL)."""
    return ReplayTick(
        ts=WINDOW_END - timedelta(seconds=10),
        btc_price=Decimal("65000"),  # == start_price → delta 0
        book_up=_ob(UP, asks=[("0.50", "100")], bids=[("0.49", "100")]),
        book_down=_ob(DOWN, asks=[("0.50", "100")], bids=[("0.49", "100")]),
    )


class TestObserveClassification:
    def test_no_signal_when_edge_below_min(self) -> None:
        engine = ReplayEngine(_config())
        _res, _diag, obs = engine.observe(_round(), [_flat_tick()], bankroll=Decimal("200"))
        assert obs.classification == ROUND_NO_SIGNAL
        assert obs.enter_orders_yielded == 0
        assert obs.fills == 0

    def test_filled_when_book_full(self) -> None:
        engine = ReplayEngine(_config())
        res, _diag, obs = engine.observe(_round(), _filled_ticks(), bankroll=Decimal("200"))
        assert obs.classification == ROUND_FILLED
        assert obs.fills >= 1
        assert obs.enter_orders_yielded >= 1
        assert res is not None

    def test_signal_no_fill_when_leader_book_empty(self) -> None:
        # EnterOrder keluar (edge lolos di decision tick) tapi ASK pemimpin KOSONG
        # di exec_tick (t+latency) → FOK gagal → SIGNAL_NO_FILL.
        engine = ReplayEngine(_config())
        res, _diag, obs = engine.observe(_round(), _signal_no_fill_ticks(), bankroll=Decimal("200"))
        assert obs.classification == ROUND_SIGNAL_NO_FILL
        assert obs.enter_orders_yielded >= 1
        assert obs.fills == 0
        assert obs.fok_rejected_empty_book >= 1
        assert res is None


class TestRunAggregate:
    def test_mixed_three_rounds(self) -> None:
        engine = ReplayEngine(_config())
        rounds = [
            (_round(0), [_flat_tick()]),  # NO_SIGNAL
            (_round(1), _filled_ticks()),  # FILLED
            (_round(2), _signal_no_fill_ticks()),  # SIGNAL_NO_FILL
        ]
        summary = engine.run(rounds)
        assert summary.rounds_filled == 1
        assert summary.rounds_signal_no_fill == 1
        assert summary.rounds_no_signal == 1
        assert summary.enter_orders_yielded >= 2  # FILLED + SIGNAL_NO_FILL
        assert summary.fills_total == 1
        assert summary.fok_rejected_empty_book >= 1
        # signal_no_fill_rate = 1 / (1 filled + 1 signal_no_fill) = 0.5
        assert summary.signal_no_fill_rate == Decimal("0.5")
        # R-A6: rounds_entered lama == rounds_filled baru.
        assert summary.rounds_entered == summary.rounds_filled == 1

    def test_report_renders_metrics(self) -> None:
        engine = ReplayEngine(_config())
        rounds = [
            (_round(1), _filled_ticks()),
            (_round(2), _signal_no_fill_ticks()),
        ]
        text = format_report(build_report(engine.run(rounds), Decimal("200")))
        assert "fill-failure" in text
        assert "signal_no_fill_rate" in text
        assert "fok_rejected_empty_book" in text


class TestStreamingParity:
    def test_accumulator_matches_run(self) -> None:
        cfg = _config()
        rounds = [
            (_round(0), [_flat_tick()]),
            (_round(1), _filled_ticks()),
            (_round(2), _signal_no_fill_ticks()),
        ]
        run_summary = ReplayEngine(cfg).run(rounds)
        acc = RunAccumulator(cfg)
        for rnd, ticks in rounds:
            acc.feed(rnd, ticks)
        stream_summary = acc.summary()
        # observability identik antara run() & RunAccumulator (streaming B7).
        assert stream_summary.rounds_filled == run_summary.rounds_filled
        assert stream_summary.rounds_signal_no_fill == run_summary.rounds_signal_no_fill
        assert stream_summary.rounds_no_signal == run_summary.rounds_no_signal
        assert stream_summary.enter_orders_yielded == run_summary.enter_orders_yielded
        assert stream_summary.fills_total == run_summary.fills_total
        assert stream_summary.fok_rejected_empty_book == run_summary.fok_rejected_empty_book
