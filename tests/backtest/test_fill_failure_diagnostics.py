"""Task G3 — fill-failure diagnostics (klasifikasi sebab NO_FILL). Observability murni.

Dua lapis uji:
1. ``classify_no_fill`` langsung — cakupan SETIAP alasan (cermin ``simulate_fill``).
2. Jalur engine (``observe``/``run``/``RunAccumulator``) — perekaman, agregasi,
   rendering report, paritas streaming, dan stabilitas output.

Tidak ada perubahan perilaku trading: ``classify_no_fill`` dipanggil HANYA saat
fill = 0, dan untuk tiap skenario kami pastikan ``simulate_fill`` memang gagal-fill.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.backtest.replay import (
    FILL_FAIL_BEST_ABOVE_LIMIT,
    FILL_FAIL_EMPTY_BOOK,
    FILL_FAIL_INSUFFICIENT_DEPTH_FOK,
    FILL_FAIL_KEYS,
    FILL_FAIL_NO_LEVEL_WITHIN_LIMIT,
    FILL_FAIL_REQUESTED_SIZE_ZERO,
    FILL_FAIL_UNKNOWN,
    ReplayConfig,
    ReplayEngine,
    ReplayTick,
    RunAccumulator,
    classify_no_fill,
    simulate_fill,
)
from btcbot.backtest.report import build_report, format_report
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus
from btcbot.domain.strategy import SIDE_BUY, StrategyParams
from btcbot.exec.sizing import SizingLimits

WINDOW_END = datetime(2026, 6, 26, 13, 20, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)
UP = "up-tok"
DOWN = "down-tok"


def _book(asks: list[tuple[str, str]]) -> OrderBook:
    """OrderBook dengan level ASK eksplisit (BID dummy)."""
    return OrderBook(
        token_id=UP,
        ts=WINDOW_START,
        bids=[BookLevel(Decimal("0.05"), Decimal("100"))],
        asks=[BookLevel(Decimal(p), Decimal(s)) for p, s in asks],
    )


# ----- Lapis 1: classify_no_fill langsung (cakupan setiap alasan) -----


class TestClassifyNoFill:
    def test_requested_size_zero(self) -> None:
        book = _book([("0.90", "100")])
        assert (
            classify_no_fill(
                book=book,
                side=SIDE_BUY,
                limit_price=Decimal("0.95"),
                requested_size=Decimal("0"),
                order_type="FOK",
            )
            == FILL_FAIL_REQUESTED_SIZE_ZERO
        )

    def test_empty_book(self) -> None:
        book = _book([])
        assert (
            classify_no_fill(
                book=book,
                side=SIDE_BUY,
                limit_price=Decimal("0.95"),
                requested_size=Decimal("5"),
                order_type="FOK",
            )
            == FILL_FAIL_EMPTY_BOOK
        )
        # cermin: simulate_fill juga gagal.
        assert not simulate_fill(
            book=book,
            side=SIDE_BUY,
            limit_price=Decimal("0.95"),
            requested_size=Decimal("5"),
            order_type="FOK",
        ).filled

    def test_best_price_above_limit(self) -> None:
        book = _book([("0.95", "100")])
        assert (
            classify_no_fill(
                book=book,
                side=SIDE_BUY,
                limit_price=Decimal("0.90"),
                requested_size=Decimal("5"),
                order_type="FOK",
            )
            == FILL_FAIL_BEST_ABOVE_LIMIT
        )
        assert not simulate_fill(
            book=book,
            side=SIDE_BUY,
            limit_price=Decimal("0.90"),
            requested_size=Decimal("5"),
            order_type="FOK",
        ).filled

    def test_no_level_within_limit(self) -> None:
        # competition_fraction=1 → factor 0 → tak ada depth tersedia (best in-range).
        book = _book([("0.90", "100")])
        assert (
            classify_no_fill(
                book=book,
                side=SIDE_BUY,
                limit_price=Decimal("0.95"),
                requested_size=Decimal("5"),
                order_type="FOK",
                competition_fraction=Decimal("1"),
            )
            == FILL_FAIL_NO_LEVEL_WITHIN_LIMIT
        )
        assert not simulate_fill(
            book=book,
            side=SIDE_BUY,
            limit_price=Decimal("0.95"),
            requested_size=Decimal("5"),
            order_type="FOK",
            competition_fraction=Decimal("1"),
        ).filled

    def test_insufficient_depth_fok(self) -> None:
        # FOK butuh 10 share tapi depth in-range hanya 5.
        book = _book([("0.90", "5")])
        assert (
            classify_no_fill(
                book=book,
                side=SIDE_BUY,
                limit_price=Decimal("0.95"),
                requested_size=Decimal("10"),
                order_type="FOK",
            )
            == FILL_FAIL_INSUFFICIENT_DEPTH_FOK
        )
        assert not simulate_fill(
            book=book,
            side=SIDE_BUY,
            limit_price=Decimal("0.95"),
            requested_size=Decimal("10"),
            order_type="FOK",
        ).filled

    def test_unknown_via_ignore_depth(self) -> None:
        # ignore_depth=True + best in-range → simulate_fill mengisi penuh; no-fill
        # mustahil di jalur ini → UNKNOWN (guard defensif).
        book = _book([("0.90", "100")])
        assert (
            classify_no_fill(
                book=book,
                side=SIDE_BUY,
                limit_price=Decimal("0.95"),
                requested_size=Decimal("5"),
                order_type="FOK",
                ignore_depth=True,
            )
            == FILL_FAIL_UNKNOWN
        )

    def test_every_key_reachable(self) -> None:
        # Sanity: keenam alasan ada di FILL_FAIL_KEYS.
        assert set(FILL_FAIL_KEYS) == {
            FILL_FAIL_EMPTY_BOOK,
            FILL_FAIL_BEST_ABOVE_LIMIT,
            FILL_FAIL_NO_LEVEL_WITHIN_LIMIT,
            FILL_FAIL_INSUFFICIENT_DEPTH_FOK,
            FILL_FAIL_REQUESTED_SIZE_ZERO,
            FILL_FAIL_UNKNOWN,
        }


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


def _config(*, competition: str = "0", min_order: str = "1") -> ReplayConfig:
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
        competition_fraction=Decimal(competition),
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


def _failures(config: ReplayConfig, ticks: list[ReplayTick]) -> dict[str, int]:
    _res, _diag, obs = ReplayEngine(config).observe(_round(), ticks, bankroll=Decimal("200"))
    return obs.fill_failures


class TestEngineFillFailure:
    def test_insufficient_depth_fok_via_competition(self) -> None:
        # EnterOrder dipancarkan (gerbang lolos) tapi kompetisi 0.99 menyisakan
        # depth < requested → FOK gagal → INSUFFICIENT_DEPTH_FOK.
        cfg = _config(competition="0.99")
        f = _failures(cfg, [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])])
        assert f[FILL_FAIL_INSUFFICIENT_DEPTH_FOK] == 1
        assert sum(f.values()) == 1

    def test_requested_size_zero_via_thin_depth(self) -> None:
        # Depth sangat tipis → sized dibulatkan < min_order_size → sized=0.
        cfg = _config()
        f = _failures(cfg, [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "0.001")])])
        assert f[FILL_FAIL_REQUESTED_SIZE_ZERO] == 1
        assert sum(f.values()) == 1

    def test_filled_round_records_no_failure(self) -> None:
        cfg = _config()
        f = _failures(cfg, [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])])
        assert sum(f.values()) == 0


class TestAggregateAndReport:
    def test_run_aggregates_failures(self) -> None:
        cfg = _config(competition="0.99")
        rounds = [
            (_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])]),
            (_round(), [_tick(ts_offset=10, price="65130", up_asks=[("0.90", "100")])]),
        ]
        summary = ReplayEngine(cfg).run(rounds)
        assert summary.fill_failure_counts[FILL_FAIL_INSUFFICIENT_DEPTH_FOK] == 2

    def test_report_renders_section(self) -> None:
        cfg = _config(competition="0.99")
        rounds = [(_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])])]
        text = format_report(build_report(ReplayEngine(cfg).run(rounds), Decimal("200")))
        assert "=== FILL FAILURE DIAGNOSTICS ===" in text
        for key in FILL_FAIL_KEYS:
            assert key in text

    def test_report_output_stable(self) -> None:
        cfg = _config(competition="0.99")
        rounds = [(_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])])]
        a = format_report(build_report(ReplayEngine(cfg).run(rounds), Decimal("200")))
        b = format_report(build_report(ReplayEngine(cfg).run(rounds), Decimal("200")))
        assert a == b


class TestStreamingParity:
    def test_accumulator_matches_run(self) -> None:
        cfg = _config(competition="0.99")
        rounds = [
            (_round(), [_tick(ts_offset=10, price="65120", up_asks=[("0.90", "100")])]),
            (_round(), [_tick(ts_offset=10, price="65130", up_asks=[("0.90", "100")])]),
        ]
        run_counts = ReplayEngine(cfg).run(rounds).fill_failure_counts
        acc = RunAccumulator(cfg)
        for rnd, ticks in rounds:
            acc.feed(rnd, ticks)
        assert acc.summary().fill_failure_counts == run_counts
