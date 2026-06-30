"""Task G4 — sizing diagnostics (mengapa size() = 0). Observability MURNI.

Dua lapis:
1. ``diagnose_size`` langsung — cap binding (Kelly/Notional/Bankroll/Depth) +
   klasifikasi min-order (RAW_BELOW_MIN / ROUNDED_BELOW_MIN / SUCCESS).
2. Jalur engine (``observe``/``run``/``RunAccumulator``) — agregasi, rendering
   report, paritas streaming, output deterministik.

``diagnose_size`` adalah cermin read-only ``size()``: untuk setiap kasus kami
pastikan nilai ``rounded_size``/``classification`` konsisten dengan ``size()``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.backtest.replay import (
    ReplayConfig,
    ReplayEngine,
    ReplayTick,
    RunAccumulator,
    SizingStat,
    _PSquareQuantile,
    _StreamStats,
)
from btcbot.backtest.report import build_report, format_report
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus, Signal
from btcbot.domain.strategy import StrategyParams
from btcbot.exec.sizing import (
    SIZING_BINDING_KEYS,
    SIZING_CLASS_KEYS,
    SIZING_RAW_BELOW_MIN,
    SIZING_ROUNDED_BELOW_MIN,
    SIZING_SUCCESS,
    BindingCap,
    SizingLimits,
    diagnose_size,
    size,
)

WINDOW_END = datetime(2026, 6, 26, 13, 20, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)
UP = "up-tok"
DOWN = "down-tok"


def _signal(*, net_edge: str, p_win: str, ask: str) -> Signal:
    return Signal(
        round_no=1,
        ts=WINDOW_START,
        price_now=Decimal("65000"),
        delta=Decimal("100"),
        time_left_sec=10,
        p_win=Decimal(p_win),
        leader=Outcome.UP.value,
        ask_win=Decimal(ask),
        net_edge=Decimal(net_edge),
    )


def _limits(  # noqa: PLR0913 - helper test (semua keyword, default aman)
    *,
    kelly: str = "0.25",
    max_notional: str = "5",
    max_bankroll: str = "0.02",
    fill_safety: str = "0.8",
    min_edge: str = "0.01",
    max_price: str = "0.99",
    min_order: str = "1",
    tick: str = "0.01",
) -> SizingLimits:
    return SizingLimits(
        kelly_fraction=Decimal(kelly),
        max_notional_round=Decimal(max_notional),
        max_bankroll_fraction=Decimal(max_bankroll),
        fill_safety=Decimal(fill_safety),
        min_edge=Decimal(min_edge),
        max_price=Decimal(max_price),
        min_order_size=Decimal(min_order),
        tick_size=Decimal(tick),
    )


# ----- Lapis 1: diagnose_size langsung -----


class TestDiagnoseBindingCap:
    def test_kelly_binding(self) -> None:
        # Kelly kecil (kelly_fraction tiny) → Kelly cap < cap lain.
        sig = _signal(net_edge="0.05", p_win="0.90", ask="0.85")
        lim = _limits(kelly="0.0001", max_notional="1000", max_bankroll="1", min_order="0")
        d = diagnose_size(sig, Decimal("1000"), Decimal("1000"), lim)
        assert d.binding_cap is BindingCap.KELLY
        assert d.binding_label == "KELLY"

    def test_notional_binding(self) -> None:
        # max_notional kecil → cap_notional mengikat.
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(kelly="1", max_notional="1", max_bankroll="1", min_order="0")
        d = diagnose_size(sig, Decimal("100000"), Decimal("100000"), lim)
        assert d.binding_cap is BindingCap.NOTIONAL

    def test_bankroll_binding(self) -> None:
        # bankroll kecil * fraction kecil → cap_bankroll mengikat.
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(kelly="1", max_notional="1000", max_bankroll="0.001", min_order="0")
        d = diagnose_size(sig, Decimal("10"), Decimal("100000"), lim)
        assert d.binding_cap is BindingCap.BANKROLL_FRACTION
        assert d.binding_label == "BANKROLL"

    def test_depth_binding(self) -> None:
        # depth kecil → cap_depth mengikat.
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(kelly="1", max_notional="1000", max_bankroll="1", min_order="0")
        d = diagnose_size(sig, Decimal("100000"), Decimal("2"), lim)
        assert d.binding_cap is BindingCap.DEPTH


class TestDiagnoseClassification:
    def test_raw_below_min(self) -> None:
        # depth kecil → raw < min_order_size (1).
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(min_order="1", tick="0.01")
        d = diagnose_size(sig, Decimal("200"), Decimal("0.001"), lim)
        assert d.classification == SIZING_RAW_BELOW_MIN
        assert size(sig, Decimal("200"), Decimal("0.001"), lim) == Decimal("0")

    def test_rounded_below_min(self) -> None:
        # raw >= min tapi tick besar membuat rounded turun < min.
        # raw ~1.4 (depth 1.8*0.8=1.44), min=1, tick=2 → round_to_tick(1.44,2)=0 < 1.
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(kelly="1", max_notional="1000", max_bankroll="1", min_order="1", tick="2")
        d = diagnose_size(sig, Decimal("100000"), Decimal("1.8"), lim)
        assert d.raw_size >= d.min_order_size
        assert d.rounded_size < d.min_order_size
        assert d.classification == SIZING_ROUNDED_BELOW_MIN
        assert size(sig, Decimal("100000"), Decimal("1.8"), lim) == Decimal("0")

    def test_success(self) -> None:
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(kelly="1", max_notional="1000", max_bankroll="1", min_order="1", tick="0.01")
        d = diagnose_size(sig, Decimal("100000"), Decimal("100"), lim)
        assert d.classification == SIZING_SUCCESS
        # cermin: size() > 0 dan == rounded_size.
        assert size(sig, Decimal("100000"), Decimal("100"), lim) == d.rounded_size > Decimal("0")

    def test_cap_values_populated(self) -> None:
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(min_order="0")
        d = diagnose_size(sig, Decimal("1000"), Decimal("50"), lim)
        assert d.cap_notional == Decimal("5") / Decimal("0.50")
        assert d.cap_depth == Decimal("50") * Decimal("0.8")
        assert d.cap_bankroll == (Decimal("1000") * Decimal("0.02")) / Decimal("0.50")
        assert d.size_kelly > Decimal("0")


# ----- streaming stats (P-square) -----


def _exact_percentile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    pos = q * (len(s) - 1)
    lo = int(pos)
    frac = pos - lo
    return s[lo] + frac * (s[lo + 1] - s[lo]) if lo + 1 < len(s) else s[lo]


class TestStreamStats:
    def test_exact_for_small_n(self) -> None:
        st = _StreamStats()
        for v in [Decimal("3"), Decimal("1"), Decimal("2")]:
            st.add(v)
        s = st.summary()
        assert s.count == 3
        assert s.minimum == Decimal("1")
        assert s.maximum == Decimal("3")
        assert s.mean == Decimal("2")
        assert s.median == Decimal("2")

    def test_psquare_approximates_median(self) -> None:
        # 1..1000 → median ~500.5, P-square harus dekat.
        est = _PSquareQuantile(0.5)
        data = [float(i) for i in range(1, 1001)]
        for v in data:
            est.add(v)
        approx = est.value()
        exact = _exact_percentile(data, 0.5)
        assert abs(approx - exact) / exact < 0.02

    def test_empty_stats(self) -> None:
        assert _StreamStats().summary() == SizingStat()


# ----- Lapis 2: jalur engine -----


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


def _config(*, min_notional: str = "5") -> ReplayConfig:
    return ReplayConfig(
        limits=SizingLimits(
            kelly_fraction=Decimal("0.25"),
            max_notional_round=Decimal(min_notional),
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


def _ob(token: str, *, asks: list[tuple[str, str]]) -> OrderBook:
    return OrderBook(
        token_id=token,
        ts=WINDOW_START,
        bids=[BookLevel(Decimal("0.05"), Decimal("100"))],
        asks=[BookLevel(Decimal(p), Decimal(s)) for p, s in asks],
    )


def _tick(*, ts_offset: int, price: str, up_asks: list[tuple[str, str]]) -> ReplayTick:
    return ReplayTick(
        ts=WINDOW_END - timedelta(seconds=ts_offset),
        btc_price=Decimal(price),
        book_up=_ob(UP, asks=up_asks),
        book_down=_ob(DOWN, asks=[("0.10", "100")]),
    )


class TestEngineSizing:
    def test_records_binding_and_class(self) -> None:
        # depth tipis → raw < min → RAW_BELOW_MIN; depth cap binding.
        cfg = _config()
        ticks = [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "0.001")])]
        _r, _d, obs = ReplayEngine(cfg).observe(_round(), ticks, bankroll=Decimal("200"))
        assert obs.sizing_class[SIZING_RAW_BELOW_MIN] == 1
        assert obs.sizing_binding["DEPTH"] == 1
        assert len(obs.sizing_samples) == 1

    def test_success_records_success(self) -> None:
        cfg = _config()
        ticks = [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])]
        _r, _d, obs = ReplayEngine(cfg).observe(_round(), ticks, bankroll=Decimal("200"))
        assert obs.sizing_class[SIZING_SUCCESS] == 1


class TestAggregateAndReport:
    def test_run_aggregates(self) -> None:
        cfg = _config()
        rounds = [
            (_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "0.001")])]),
            (_round(), [_tick(ts_offset=10, price="65130", up_asks=[("0.90", "0.001")])]),
        ]
        summary = ReplayEngine(cfg).run(rounds)
        assert summary.sizing_class_counts[SIZING_RAW_BELOW_MIN] == 2
        assert summary.sizing_binding_counts["DEPTH"] == 2
        assert summary.sizing_raw_stats.count == 2

    def test_report_renders_section(self) -> None:
        cfg = _config()
        rounds = [(_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "0.001")])])]
        text = format_report(build_report(ReplayEngine(cfg).run(rounds), Decimal("200")))
        assert "=== SIZING DIAGNOSTICS ===" in text
        assert "Binding cap:" in text
        assert "Minimum order:" in text
        assert "Raw size" in text
        assert "Rounded size" in text
        assert "Kelly cap" in text
        for key in SIZING_BINDING_KEYS:
            assert key in text
        for key in SIZING_CLASS_KEYS:
            assert key in text

    def test_report_output_stable(self) -> None:
        cfg = _config()
        rounds = [(_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "0.001")])])]
        a = format_report(build_report(ReplayEngine(cfg).run(rounds), Decimal("200")))
        b = format_report(build_report(ReplayEngine(cfg).run(rounds), Decimal("200")))
        assert a == b


class TestStreamingParity:
    def test_accumulator_matches_run(self) -> None:
        cfg = _config()
        rounds = [
            (_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "0.001")])]),
            (_round(), [_tick(ts_offset=10, price="65130", up_asks=[("0.90", "100")])]),
        ]
        run_summary = ReplayEngine(cfg).run(rounds)
        acc = RunAccumulator(cfg)
        for rnd, ticks in rounds:
            acc.feed(rnd, ticks)
        acc_summary = acc.summary()
        assert acc_summary.sizing_binding_counts == run_summary.sizing_binding_counts
        assert acc_summary.sizing_class_counts == run_summary.sizing_class_counts
        assert acc_summary.sizing_raw_stats == run_summary.sizing_raw_stats
        assert acc_summary.sizing_rounded_stats == run_summary.sizing_rounded_stats
