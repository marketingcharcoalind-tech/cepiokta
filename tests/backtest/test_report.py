"""Unit tests for btcbot.backtest.report (metrics, reliability, grid, ablation)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.backtest.replay import (
    ReplayConfig,
    ReplaySummary,
    ReplayTick,
    RoundDiagnostics,
)
from btcbot.backtest.report import (
    ablation,
    build_report,
    compute_distribution,
    compute_reliability,
    format_ablation,
    format_grid,
    format_report,
    max_drawdown,
    sensitivity_grid,
)
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundResult, RoundStatus
from btcbot.domain.strategy import StrategyParams
from btcbot.exec.sizing import SizingLimits

WINDOW_END = datetime(2026, 6, 26, 13, 20, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)
UP = "up-tok"
DOWN = "down-tok"


# ---------- pure metric helpers ----------


class TestDistribution:
    def test_basic_stats(self) -> None:
        dist = compute_distribution([Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")])
        assert dist.count == 4
        assert dist.minimum == Decimal("1")
        assert dist.maximum == Decimal("4")
        assert dist.mean == Decimal("2.5")
        assert dist.median == Decimal("2.5")

    def test_empty(self) -> None:
        dist = compute_distribution([])
        assert dist.count == 0
        assert dist.mean == Decimal("0")


class TestMaxDrawdown:
    def test_drawdown_peak_to_trough(self) -> None:
        # peak 120, trough 90 → dd 30 (25%)
        dd, pct = max_drawdown([Decimal("100"), Decimal("120"), Decimal("90"), Decimal("110")])
        assert dd == Decimal("30")
        assert pct == Decimal("25")

    def test_monotonic_up_no_drawdown(self) -> None:
        dd, pct = max_drawdown([Decimal("100"), Decimal("110"), Decimal("120")])
        assert dd == Decimal("0")
        assert pct == Decimal("0")


class TestReliability:
    def _diag(self, p_win: str, won: bool) -> RoundDiagnostics:
        return RoundDiagnostics(
            round_no=1,
            p_win_entry=Decimal(p_win),
            net_edge_entry=Decimal("0.05"),
            won=won,
            pnl=Decimal("0.1") if won else Decimal("-0.1"),
        )

    def test_buckets_predicted_vs_realized(self) -> None:
        diags = [
            self._diag("0.92", True),
            self._diag("0.95", True),
            self._diag("0.93", False),  # bin [0.9,1.0): 3 entri, 2 menang
            self._diag("0.55", True),  # bin [0.5,0.6): 1 entri, 1 menang
        ]
        buckets = compute_reliability(diags, n_bins=5)
        assert len(buckets) == 5
        last = buckets[-1]  # [0.9, 1.0]
        assert last.count == 3
        assert last.realized == Decimal("2") / Decimal("3")
        first = buckets[0]  # [0.5, 0.6)
        assert first.count == 1
        assert first.realized == Decimal("1")

    def test_empty_bins_zero(self) -> None:
        buckets = compute_reliability([], n_bins=5)
        assert all(b.count == 0 for b in buckets)


# ---------- end-to-end report from a synthetic summary ----------


def _result(round_no: int, pnl: str, balance: str, *, settled: str = "0") -> RoundResult:
    return RoundResult(
        round_no=round_no,
        side_taken="UP",
        entry_price=Decimal("0.90"),
        size=Decimal("4"),
        hedge_cost=Decimal("0"),
        settled=Decimal(settled),
        pnl=Decimal(pnl),
        balance_after=Decimal(balance),
    )


def _diag(round_no: int, p_win: str, net_edge: str, won: bool, pnl: str) -> RoundDiagnostics:
    return RoundDiagnostics(
        round_no=round_no,
        p_win_entry=Decimal(p_win),
        net_edge_entry=Decimal(net_edge),
        won=won,
        pnl=Decimal(pnl),
    )


class TestBuildReport:
    def test_headline_metrics(self) -> None:
        summary = ReplaySummary(
            rounds_total=3,
            rounds_entered=2,
            wins=1,
            losses=1,
            total_pnl=Decimal("0.5"),
            final_balance=Decimal("200.5"),
            results=(_result(1, "1.0", "201"), _result(2, "-0.5", "200.5")),
            diagnostics=(
                _diag(1, "0.95", "0.06", won=True, pnl="1.0"),
                _diag(2, "0.92", "0.04", won=False, pnl="-0.5"),
            ),
        )
        rep = build_report(summary, Decimal("200"))
        assert rep.net_pnl == Decimal("0.5")
        assert rep.roi == Decimal("0.5") / Decimal("200")
        assert rep.win_rate == Decimal("0.5")
        assert rep.net_edge_dist.count == 2
        assert rep.pnl_stdev >= Decimal("0")
        # drawdown: peak 201 → 200.5 = 0.5
        assert rep.max_drawdown == Decimal("0.5")

    def test_format_report_is_text(self) -> None:
        summary = ReplaySummary(
            rounds_total=1,
            rounds_entered=1,
            wins=1,
            losses=0,
            total_pnl=Decimal("1"),
            final_balance=Decimal("201"),
            results=(_result(1, "1", "201"),),
            diagnostics=(_diag(1, "0.95", "0.06", won=True, pnl="1"),),
        )
        text = format_report(build_report(summary, Decimal("200")))
        assert "Net PnL (setelah fee)" in text
        assert "reliability curve" in text


# ---------- grid & ablation (re-run engine) ----------


def make_round(outcome: Outcome) -> Round:
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
        resolved_outcome=outcome,
    )


def winning_ticks() -> list[ReplayTick]:
    b_up = OrderBook(
        token_id=UP,
        ts=WINDOW_START,
        bids=[BookLevel(Decimal("0.88"), Decimal("100"))],
        asks=[BookLevel(Decimal("0.90"), Decimal("100"))],
    )
    b_down = OrderBook(
        token_id=DOWN,
        ts=WINDOW_START,
        bids=[BookLevel(Decimal("0.08"), Decimal("100"))],
        asks=[BookLevel(Decimal("0.12"), Decimal("100"))],
    )
    return [
        ReplayTick(
            ts=WINDOW_END - timedelta(seconds=10),
            btc_price=Decimal("65100"),
            book_up=b_up,
            book_down=b_down,
        )
    ]


def make_config() -> ReplayConfig:
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
        latency_ticks=0,
        seed=42,
    )


class TestSensitivityGrid:
    def test_grid_covers_all_combos(self) -> None:
        rounds = [(make_round(Outcome.UP), winning_ticks())]
        cells = sensitivity_grid(
            rounds,
            make_config(),
            t_entry_values=[10, 20],
            delta_values=[Decimal("1"), Decimal("50")],
            max_price_values=[Decimal("0.99")],
        )
        assert len(cells) == 4  # 2 x 2 x 1
        # delta 50 > Δ100? Δ=100 ≥ 50 → entry tetap; delta besar bisa menyaring.
        assert any(c.rounds_entered >= 1 for c in cells)

    def test_format_grid_text(self) -> None:
        rounds = [(make_round(Outcome.UP), winning_ticks())]
        cells = sensitivity_grid(
            rounds,
            make_config(),
            t_entry_values=[20],
            delta_values=[Decimal("1")],
            max_price_values=[Decimal("0.99")],
        )
        assert "SENSITIVITY GRID" in format_grid(cells)


class TestAblation:
    def test_no_fee_beats_baseline(self) -> None:
        rounds = [(make_round(Outcome.UP), winning_ticks())]
        rows = ablation(rounds, make_config())
        names = {r.name for r in rows}
        assert "no_fee" in names
        assert "no_slippage" in names
        assert "no_latency" in names
        baseline = next(r for r in rows if r.name.startswith("baseline"))
        no_fee = next(r for r in rows if r.name == "no_fee")
        # Tanpa fee → PnL >= baseline (fee mengurangi PnL).
        assert no_fee.net_pnl >= baseline.net_pnl

    def test_format_ablation_text(self) -> None:
        rounds = [(make_round(Outcome.UP), winning_ticks())]
        text = format_ablation(ablation(rounds, make_config()))
        assert "ABLATION" in text
