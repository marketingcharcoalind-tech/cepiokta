"""Task G2 — entry-decision diagnostics (alasan Strategy NoOp). Observability murni."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.backtest.replay import (
    ENTRY_REASON_ASK_HIGH,
    ENTRY_REASON_ASK_LOW,
    ENTRY_REASON_DELTA,
    ENTRY_REASON_EDGE,
    ENTRY_REASON_ENTER,
    ENTRY_REASON_TIME_LEFT,
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


def _ob(token: str, *, asks: list[tuple[str, str]]) -> OrderBook:
    return OrderBook(
        token_id=token,
        ts=WINDOW_START,
        bids=[BookLevel(Decimal("0.05"), Decimal("100"))],
        asks=[BookLevel(Decimal(p), Decimal(s)) for p, s in asks],
    )


def _round() -> Round:
    return Round(
        condition_id="0xc",
        round_no=1782480000,
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


def _config(*, delta_threshold: str = "1", latency: int = 0) -> ReplayConfig:
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
            delta_threshold=Decimal(delta_threshold),
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


def _tick(*, ts_offset: int, price: str, up_asks: list[tuple[str, str]]) -> ReplayTick:
    return ReplayTick(
        ts=WINDOW_END - timedelta(seconds=ts_offset),
        btc_price=Decimal(price),
        book_up=_ob(UP, asks=up_asks),
        book_down=_ob(DOWN, asks=[("0.10", "100")]),
    )


def _counts(config: ReplayConfig, ticks: list[ReplayTick]) -> dict[str, int]:
    _res, _diag, obs = ReplayEngine(config).observe(_round(), ticks, bankroll=Decimal("200"))
    return obs.entry_reasons


class TestEntryReasonCounts:
    def test_time_left_gate(self) -> None:
        # 120s tersisa > T_ENTRY 20 → NoOp time_left>t_entry.
        c = _counts(_config(), [_tick(ts_offset=120, price="65120", up_asks=[("0.90", "100")])])
        assert c[ENTRY_REASON_TIME_LEFT] == 1
        assert c[ENTRY_REASON_ENTER] == 0

    def test_delta_gate(self) -> None:
        # delta 50 < threshold 200 → NoOp abs_delta<threshold.
        c = _counts(
            _config(delta_threshold="200"),
            [_tick(ts_offset=10, price="65050", up_asks=[("0.90", "100")])],
        )
        assert c[ENTRY_REASON_DELTA] == 1
        assert c[ENTRY_REASON_ENTER] == 0

    def test_ask_below_min_price(self) -> None:
        # ask 0.70 < min_price 0.80 → NoOp ask<min_price.
        c = _counts(_config(), [_tick(ts_offset=10, price="65120", up_asks=[("0.70", "100")])])
        assert c[ENTRY_REASON_ASK_LOW] == 1
        assert c[ENTRY_REASON_ENTER] == 0

    def test_ask_above_max_price(self) -> None:
        # ask 0.995 > max_price 0.99 → NoOp ask>max_price (anti-chase).
        c = _counts(_config(), [_tick(ts_offset=10, price="65120", up_asks=[("0.995", "100")])])
        assert c[ENTRY_REASON_ASK_HIGH] == 1
        assert c[ENTRY_REASON_ENTER] == 0

    def test_ask_above_max_when_empty_book(self) -> None:
        # book pemimpin kosong → ask_win=1 > max_price → ask>max_price.
        c = _counts(_config(), [_tick(ts_offset=10, price="65120", up_asks=[])])
        assert c[ENTRY_REASON_ASK_HIGH] == 1

    def test_net_edge_gate(self) -> None:
        # Edge tipis: vol besar → p_win rendah → net_edge<min_edge meski ask in band.
        cfg = _config()
        cfg = ReplayConfig(
            limits=cfg.limits,
            params=cfg.params,
            vol=Decimal("100000"),
            starting_balance=cfg.starting_balance,
            latency_ticks=0,
        )
        c = _counts(cfg, [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])])
        assert c[ENTRY_REASON_EDGE] == 1
        assert c[ENTRY_REASON_ENTER] == 0

    def test_enter_when_all_gates_pass(self) -> None:
        c = _counts(_config(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])])
        assert c[ENTRY_REASON_ENTER] == 1


class TestAggregateAndReport:
    def test_run_aggregates_reasons(self) -> None:
        engine = ReplayEngine(_config())
        rounds = [
            (_round(), [_tick(ts_offset=120, price="65120", up_asks=[("0.90", "100")])]),  # tl
            (_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.70", "100")])]),  # ask low
            (_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])]),  # ENTER
        ]
        summary = engine.run(rounds)
        assert summary.entry_reason_counts[ENTRY_REASON_TIME_LEFT] == 1
        assert summary.entry_reason_counts[ENTRY_REASON_ASK_LOW] == 1
        assert summary.entry_reason_counts[ENTRY_REASON_ENTER] == 1

    def test_report_renders_entry_diagnostics(self) -> None:
        engine = ReplayEngine(_config())
        rounds = [(_round(), [_tick(ts_offset=120, price="65120", up_asks=[("0.90", "100")])])]
        text = format_report(build_report(engine.run(rounds), Decimal("200")))
        assert "=== ENTRY DIAGNOSTICS ===" in text
        assert ENTRY_REASON_TIME_LEFT in text
        assert ENTRY_REASON_ENTER in text

    def test_report_output_stable(self) -> None:
        engine = ReplayEngine(_config())
        rounds = [(_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])])]
        a = format_report(build_report(engine.run(rounds), Decimal("200")))
        b = format_report(build_report(engine.run(rounds), Decimal("200")))
        assert a == b


class TestStreamingParity:
    def test_accumulator_matches_run(self) -> None:
        cfg = _config()
        rounds = [
            (_round(), [_tick(ts_offset=120, price="65120", up_asks=[("0.90", "100")])]),
            (_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])]),
        ]
        run_counts = ReplayEngine(cfg).run(rounds).entry_reason_counts
        acc = RunAccumulator(cfg)
        for rnd, ticks in rounds:
            acc.feed(rnd, ticks)
        assert acc.summary().entry_reason_counts == run_counts
