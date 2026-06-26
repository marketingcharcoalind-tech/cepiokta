"""Unit tests for btcbot.backtest.replay (fill model + ReplayEngine)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.backtest.replay import (
    FillResult,
    ReplayConfig,
    ReplayEngine,
    ReplayTick,
    reconstruct_ticks,
    run_and_persist,
    simulate_fill,
)
from btcbot.data.store import BookSnapshot, Store
from btcbot.domain.fees import ProportionalTakerFee, ZeroFee
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus, Signal
from btcbot.domain.strategy import SIDE_BUY, StrategyParams
from btcbot.exec.sizing import SizingLimits

WINDOW_END = datetime(2026, 6, 26, 13, 20, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)
UP = "up-tok"
DOWN = "down-tok"


def book(
    token: str,
    *,
    asks: list[tuple[str, str]] | None = None,
    bids: list[tuple[str, str]] | None = None,
    ts: datetime = WINDOW_START,
) -> OrderBook:
    return OrderBook(
        token_id=token,
        ts=ts,
        bids=[BookLevel(Decimal(p), Decimal(s)) for p, s in (bids or [])],
        asks=[BookLevel(Decimal(p), Decimal(s)) for p, s in (asks or [])],
    )


# ---------- fill model ----------


class TestSimulateFill:
    def test_buy_walks_levels_with_slippage(self) -> None:
        b = book("t", asks=[("0.90", "5"), ("0.92", "10")])
        fr = simulate_fill(
            book=b,
            side=SIDE_BUY,
            limit_price=Decimal("0.93"),
            requested_size=Decimal("8"),
            order_type="FAK",
        )
        assert fr.filled_size == Decimal("8")
        # cost = 5*0.90 + 3*0.92 = 7.26 → avg 0.9075
        assert fr.notional == Decimal("7.26")
        assert fr.avg_price == Decimal("7.26") / Decimal("8")

    def test_buy_respects_limit_price(self) -> None:
        b = book("t", asks=[("0.90", "5"), ("0.95", "10")])
        fr = simulate_fill(
            book=b,
            side=SIDE_BUY,
            limit_price=Decimal("0.92"),
            requested_size=Decimal("8"),
            order_type="FAK",
        )
        assert fr.filled_size == Decimal("5")  # level 0.95 > limit → berhenti

    def test_fok_all_or_nothing(self) -> None:
        b = book("t", asks=[("0.90", "5")])
        fr = simulate_fill(
            book=b,
            side=SIDE_BUY,
            limit_price=Decimal("0.99"),
            requested_size=Decimal("20"),
            order_type="FOK",
        )
        assert fr == FillResult(Decimal("0"), Decimal("0"), Decimal("0"))
        assert not fr.filled

    def test_fak_partial(self) -> None:
        b = book("t", asks=[("0.90", "5")])
        fr = simulate_fill(
            book=b,
            side=SIDE_BUY,
            limit_price=Decimal("0.99"),
            requested_size=Decimal("20"),
            order_type="FAK",
        )
        assert fr.filled_size == Decimal("5")

    def test_competition_reduces_available(self) -> None:
        b = book("t", asks=[("0.90", "10")])
        fr = simulate_fill(
            book=b,
            side=SIDE_BUY,
            limit_price=Decimal("0.99"),
            requested_size=Decimal("8"),
            order_type="FAK",
            competition_fraction=Decimal("0.5"),
        )
        assert fr.filled_size == Decimal("5")  # hanya 50% surplus

    def test_sell_walks_bids(self) -> None:
        b = book("t", bids=[("0.80", "5"), ("0.78", "10")])
        fr = simulate_fill(
            book=b,
            side="SELL",
            limit_price=Decimal("0.79"),
            requested_size=Decimal("8"),
            order_type="FAK",
        )
        assert fr.filled_size == Decimal("5")  # 0.78 < limit → berhenti

    def test_empty_book_no_fill(self) -> None:
        fr = simulate_fill(
            book=book("t"),
            side=SIDE_BUY,
            limit_price=Decimal("0.99"),
            requested_size=Decimal("5"),
            order_type="FAK",
        )
        assert not fr.filled


# ---------- engine ----------


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


def make_config(**overrides: object) -> ReplayConfig:
    limits = SizingLimits(
        kelly_fraction=Decimal("0.25"),
        max_notional_round=Decimal("5"),
        max_bankroll_fraction=Decimal("0.02"),
        fill_safety=Decimal("0.8"),
        min_edge=Decimal("0.01"),
        max_price=Decimal("0.99"),
    )
    params = StrategyParams(
        t_entry_sec=20,
        delta_threshold=Decimal("1"),
        min_price=Decimal("0.80"),
        max_price=Decimal("0.99"),
        min_edge=Decimal("0.01"),
        flip_ratio=Decimal("0.90"),
        hedge_fraction=Decimal("0.5"),
        p_exit=Decimal("0.65"),
    )
    base: dict[str, object] = {
        "limits": limits,
        "params": params,
        "vol": Decimal("1"),
        "starting_balance": Decimal("200"),
        "latency_ticks": 0,
        "competition_fraction": Decimal("0"),
        "seed": 42,
    }
    base.update(overrides)
    return ReplayConfig(**base)  # type: ignore[arg-type]


def winning_ticks(price: str = "65100") -> list[ReplayTick]:
    # UP memimpin (Δ>0); 10 dtk tersisa → dalam T_ENTRY 20.
    b_up = book(UP, asks=[("0.90", "100")], bids=[("0.88", "100")])
    b_down = book(DOWN, asks=[("0.12", "100")], bids=[("0.08", "100")])
    return [
        ReplayTick(
            ts=WINDOW_END - timedelta(seconds=10),
            btc_price=Decimal(price),
            book_up=b_up,
            book_down=b_down,
        )
    ]


class TestRunRound:
    def test_winning_entry_positive_pnl(self) -> None:
        engine = ReplayEngine(make_config())
        res = engine.run_round(make_round(Outcome.UP), winning_ticks(), bankroll=Decimal("200"))
        assert res is not None
        assert res.side_taken == "UP"
        assert res.size > Decimal("0")
        assert res.settled > Decimal("0")  # token UP settle $1
        assert res.pnl > Decimal("0")

    def test_losing_entry_negative_pnl(self) -> None:
        # Sama persis, tapi label Gamma = DOWN → posisi UP kalah.
        engine = ReplayEngine(make_config())
        res = engine.run_round(make_round(Outcome.DOWN), winning_ticks(), bankroll=Decimal("200"))
        assert res is not None
        assert res.side_taken == "UP"
        assert res.settled == Decimal("0")  # UP kalah → payout 0
        assert res.pnl < Decimal("0")

    def test_settlement_uses_gamma_label_not_delta(self) -> None:
        # Δ>0 (UP memimpin) tapi label DOWN menang → pnl harus negatif.
        engine = ReplayEngine(make_config())
        win = engine.run_round(make_round(Outcome.UP), winning_ticks(), bankroll=Decimal("200"))
        lose = engine.run_round(make_round(Outcome.DOWN), winning_ticks(), bankroll=Decimal("200"))
        assert win is not None
        assert lose is not None
        assert win.pnl > Decimal("0") > lose.pnl

    def test_no_entry_when_filters_fail_returns_none(self) -> None:
        engine = ReplayEngine(make_config())
        # time_left besar (120s > T_ENTRY) → tak ada entry.
        ticks = [
            ReplayTick(
                ts=WINDOW_END - timedelta(seconds=120),
                btc_price=Decimal("65100"),
                book_up=book(UP, asks=[("0.90", "100")]),
                book_down=book(DOWN, asks=[("0.12", "100")]),
            )
        ]
        assert engine.run_round(make_round(Outcome.UP), ticks, bankroll=Decimal("200")) is None

    def test_unresolved_round_returns_none(self) -> None:
        engine = ReplayEngine(make_config())
        rnd = Round(
            condition_id="0xc",
            round_no=1,
            token_id_up=UP,
            token_id_down=DOWN,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            start_price=Decimal("65000"),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("1"),
            status=RoundStatus.ACTIVE,
            resolved_outcome=None,
        )
        assert engine.run_round(rnd, winning_ticks(), bankroll=Decimal("200")) is None

    def test_deterministic_same_inputs(self) -> None:
        res1 = ReplayEngine(make_config()).run_round(
            make_round(Outcome.UP), winning_ticks(), bankroll=Decimal("200")
        )
        res2 = ReplayEngine(make_config()).run_round(
            make_round(Outcome.UP), winning_ticks(), bankroll=Decimal("200")
        )
        assert res1 == res2

    def test_zero_fee_higher_pnl_than_with_fee(self) -> None:
        # Ablation (docs/09): tanpa fee → PnL lebih tinggi.
        res_fee = ReplayEngine(
            make_config(fee_model=ProportionalTakerFee(Decimal("0.07")))
        ).run_round(make_round(Outcome.UP), winning_ticks(), bankroll=Decimal("200"))
        res_zero = ReplayEngine(make_config(fee_model=ZeroFee())).run_round(
            make_round(Outcome.UP), winning_ticks(), bankroll=Decimal("200")
        )
        assert res_fee is not None
        assert res_zero is not None
        assert res_zero.pnl > res_fee.pnl


class TestRunMany:
    def test_equity_accumulates(self) -> None:
        engine = ReplayEngine(make_config())
        rounds = [
            (make_round(Outcome.UP), winning_ticks()),
            (make_round(Outcome.UP), winning_ticks()),
        ]
        summary = engine.run(rounds)
        assert summary.rounds_total == 2
        assert summary.rounds_entered == 2
        assert summary.wins == 2
        assert summary.final_balance == Decimal("200") + summary.total_pnl

    def test_reproducible_summary(self) -> None:
        rounds = [(make_round(Outcome.UP), winning_ticks())]
        s1 = ReplayEngine(make_config()).run(rounds)
        s2 = ReplayEngine(make_config()).run(rounds)
        assert s1 == s2


class TestReconstructTicks:
    def test_builds_ticks_from_snapshots(self) -> None:
        rnd = make_round(Outcome.UP)
        ts = WINDOW_END - timedelta(seconds=30)
        snaps = [
            BookSnapshot(
                round_no=rnd.round_no,
                token_id=UP,
                ts=ts,
                best_bid=Decimal("0.88"),
                best_ask=Decimal("0.90"),
                bid_depth=Decimal("100"),
                ask_depth=Decimal("100"),
                gap=False,
                raw=None,
                mode="readonly",
            ),
            BookSnapshot(
                round_no=rnd.round_no,
                token_id=DOWN,
                ts=ts + timedelta(seconds=1),
                best_bid=Decimal("0.08"),
                best_ask=Decimal("0.12"),
                bid_depth=Decimal("100"),
                ask_depth=Decimal("100"),
                gap=False,
                raw=None,
                mode="readonly",
            ),
        ]
        ticks = reconstruct_ticks(rnd, snaps, signals=[])
        assert len(ticks) == 2
        assert ticks[0].book_up.asks[0].price == Decimal("0.90")
        assert ticks[0].btc_price == rnd.start_price  # fallback (tanpa signal)

    def test_skips_gap_rows(self) -> None:
        rnd = make_round(Outcome.UP)
        ts = WINDOW_END - timedelta(seconds=30)
        snaps = [
            BookSnapshot(
                round_no=rnd.round_no,
                token_id="",
                ts=ts,
                best_bid=None,
                best_ask=None,
                bid_depth=None,
                ask_depth=None,
                gap=True,
                raw="disc",
                mode="readonly",
            ),
        ]
        assert reconstruct_ticks(rnd, snaps, signals=[]) == []


class TestRunAndPersist:
    async def test_writes_results_and_equity(self) -> None:
        store = await Store.open(":memory:")
        try:
            rnd = make_round(Outcome.UP)
            await store.upsert_round(rnd)
            await store.set_resolution(rnd.round_no, Outcome.UP)
            ts = WINDOW_END - timedelta(seconds=10)
            await store.insert_book_snapshot(
                rnd.round_no,
                book(UP, asks=[("0.90", "100")], bids=[("0.88", "100")], ts=ts),
                mode="readonly",
            )
            await store.insert_book_snapshot(
                rnd.round_no,
                book(DOWN, asks=[("0.12", "100")], bids=[("0.08", "100")], ts=ts),
                mode="readonly",
            )
            # Sinyal harga (BTC) agar Δ>0 di tick.
            await store.insert_signal(
                Signal(
                    round_no=rnd.round_no,
                    ts=ts,
                    price_now=Decimal("65100"),
                    delta=Decimal("100"),
                    time_left_sec=10.0,
                    p_win=Decimal("0"),
                    leader="UP",
                    ask_win=Decimal("0"),
                    net_edge=Decimal("0"),
                ),
                mode="readonly",
            )

            summary = await run_and_persist(store, make_config())

            assert summary.rounds_entered >= 1
            stored = await store.get_round_result(rnd.round_no)
            assert stored is not None
            assert stored.side_taken == "UP"
            equity = await store.get_equity_curve(mode="backtest")
            assert len(equity) >= 1
        finally:
            await store.close()
