"""Task G5 — depth diagnostics (mengapa DEPTH cap mengikat). Observability MURNI.

Memverifikasi:
1. ``diagnose_size`` merekam ``fill_safety`` & ``returned_size`` (cermin ``size()``).
2. ``_ratio_bucket`` memetakan rasio ke bucket yang benar.
3. Jalur engine: distribusi depth mentah, bucket rasio depth/min & raw/min,
   rendering report, paritas streaming, output deterministik.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.backtest.replay import (
    RATIO_BUCKET_KEYS,
    ReplayConfig,
    ReplayEngine,
    ReplayTick,
    RunAccumulator,
    _ratio_bucket,
)
from btcbot.backtest.report import build_report, format_report
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus, Signal
from btcbot.domain.strategy import StrategyParams
from btcbot.exec.sizing import SizingLimits, diagnose_size, size

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


def _limits(*, fill_safety: str = "0.8", min_order: str = "1", tick: str = "0.01") -> SizingLimits:
    return SizingLimits(
        kelly_fraction=Decimal("0.25"),
        max_notional_round=Decimal("5"),
        max_bankroll_fraction=Decimal("0.02"),
        fill_safety=Decimal(fill_safety),
        min_edge=Decimal("0.01"),
        max_price=Decimal("0.99"),
        min_order_size=Decimal(min_order),
        tick_size=Decimal(tick),
    )


# ----- Lapis 1: diagnose_size fields + ratio bucket -----


class TestDiagnoseDepthFields:
    def test_fill_safety_and_depth_after(self) -> None:
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(fill_safety="0.8", min_order="0")
        d = diagnose_size(sig, Decimal("1000"), Decimal("50"), lim)
        assert d.fill_safety == Decimal("0.8")
        assert d.depth_available == Decimal("50")
        assert d.cap_depth == Decimal("50") * Decimal("0.8")  # depth_after_fill_safety

    def test_returned_size_zero_when_below_min(self) -> None:
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(min_order="1")
        d = diagnose_size(sig, Decimal("200"), Decimal("0.001"), lim)
        assert d.returned_size == Decimal("0")
        assert size(sig, Decimal("200"), Decimal("0.001"), lim) == Decimal("0")

    def test_returned_size_matches_size(self) -> None:
        sig = _signal(net_edge="0.20", p_win="0.95", ask="0.50")
        lim = _limits(min_order="1", tick="0.01")
        d = diagnose_size(sig, Decimal("100000"), Decimal("100"), lim)
        assert d.returned_size == size(sig, Decimal("100000"), Decimal("100"), lim) > Decimal("0")


class TestRatioBucket:
    def test_buckets(self) -> None:
        assert _ratio_bucket(Decimal("0.05")) == "<0.1"
        assert _ratio_bucket(Decimal("0.3")) == "0.1-0.5"
        assert _ratio_bucket(Decimal("0.9")) == "0.5-1"
        assert _ratio_bucket(Decimal("1")) == "1-2"
        assert _ratio_bucket(Decimal("3")) == "2-5"
        assert _ratio_bucket(Decimal("7")) == "5-10"
        assert _ratio_bucket(Decimal("10")) == ">=10"
        assert _ratio_bucket(Decimal("1000")) == ">=10"

    def test_all_keys_reachable(self) -> None:
        ratios = [Decimal(x) for x in ("0.01", "0.3", "0.7", "1.5", "3", "7", "50")]
        seen = {_ratio_bucket(r) for r in ratios}
        assert seen == set(RATIO_BUCKET_KEYS)


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


def _config() -> ReplayConfig:
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


class TestEngineDepth:
    def test_thin_depth_buckets(self) -> None:
        # depth 0.001, min_order 1 → rasio depth/min = 0.001 → "<0.1".
        cfg = _config()
        ticks = [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "0.001")])]
        _r, _d, obs = ReplayEngine(cfg).observe(_round(), ticks, bankroll=Decimal("200"))
        sd = obs.sizing_samples[0]
        assert sd.depth_available == Decimal("0.001")
        assert sd.fill_safety == Decimal("0.8")

    def test_run_depth_aggregation(self) -> None:
        cfg = _config()
        rounds = [
            (_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "0.001")])]),
            (_round(), [_tick(ts_offset=10, price="65130", up_asks=[("0.90", "100")])]),
        ]
        summary = ReplayEngine(cfg).run(rounds)
        assert summary.depth_available_stats.count == 2
        assert summary.depth_available_stats.minimum == Decimal("0.001")
        assert summary.depth_available_stats.maximum == Decimal("100")
        # depth/min: 0.001 → "<0.1"; 100 → ">=10".
        assert summary.depth_ratio_buckets["<0.1"] == 1
        assert summary.depth_ratio_buckets[">=10"] == 1
        # raw/min: thin depth → raw tiny "<0.1"; deep → raw capped by notional ~5.5 → "5-10".
        assert summary.raw_ratio_buckets["<0.1"] == 1


class TestReport:
    def test_renders_depth_section(self) -> None:
        cfg = _config()
        rounds = [(_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "0.001")])])]
        text = format_report(build_report(ReplayEngine(cfg).run(rounds), Decimal("200")))
        assert "=== DEPTH DIAGNOSTICS ===" in text
        assert "depth_available" in text
        assert "depth_after_fill_safety" in text
        assert "DEPTH binding" in text
        assert "depth_available / min_order_size:" in text
        assert "raw_size / min_order_size:" in text
        for key in RATIO_BUCKET_KEYS:
            assert key in text

    def test_output_stable(self) -> None:
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
        assert acc_summary.depth_ratio_buckets == run_summary.depth_ratio_buckets
        assert acc_summary.raw_ratio_buckets == run_summary.raw_ratio_buckets
        assert acc_summary.depth_available_stats == run_summary.depth_available_stats
