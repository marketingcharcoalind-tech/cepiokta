"""Unit tests for btcbot.domain.strategy.

Cakup tiap cabang keputusan (EnterOrder/Hedge/Exit/NoOp) + anti-pattern
(docs/05 §5.7 → harus NoOp). Murni & deterministik.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from btcbot.domain.models import BookLevel, OrderBook, Outcome, Position, Signal
from btcbot.domain.strategy import (
    EnterOrder,
    Exit,
    Hedge,
    MarketBook,
    NoOp,
    Strategy,
    StrategyParams,
    best_bid,
    book_depth,
    flip_ratio,
)

TS = datetime(2026, 6, 26, 13, 19, 45, tzinfo=UTC)
UP_TOKEN = "up-tok"
DOWN_TOKEN = "down-tok"


def params(**overrides: object) -> StrategyParams:
    base: dict[str, object] = {
        "t_entry_sec": 20,
        "delta_threshold": Decimal("50"),
        "min_price": Decimal("0.80"),
        "max_price": Decimal("0.99"),
        "min_edge": Decimal("0.01"),
        "flip_ratio": Decimal("0.90"),
        "hedge_fraction": Decimal("0.5"),
        "p_exit": Decimal("0.65"),
    }
    base.update(overrides)
    return StrategyParams(**base)  # type: ignore[arg-type]


def ob(token: str, *, asks: list[str] | None = None, bids: list[str] | None = None) -> OrderBook:
    return OrderBook(
        token_id=token,
        ts=TS,
        bids=[BookLevel(Decimal(p), Decimal("100")) for p in (bids or [])],
        asks=[BookLevel(Decimal(p), Decimal("100")) for p in (asks or [])],
    )


def market(
    *,
    up_asks: list[str] | None = None,
    up_bids: list[str] | None = None,
    down_asks: list[str] | None = None,
    down_bids: list[str] | None = None,
) -> MarketBook:
    return MarketBook(
        up=ob(UP_TOKEN, asks=up_asks, bids=up_bids),
        down=ob(DOWN_TOKEN, asks=down_asks, bids=down_bids),
    )


def sig(  # noqa: PLR0913 - helper test builder (kwargs eksplisit)
    *,
    delta: str = "100",
    time_left: float = 15.0,
    p_win: str = "0.90",
    leader: Outcome = Outcome.UP,
    ask_win: str = "0.90",
    net_edge: str = "0.05",
) -> Signal:
    return Signal(
        round_no=1782480000,
        ts=TS,
        price_now=Decimal("65100"),
        delta=Decimal(delta),
        time_left_sec=time_left,
        p_win=Decimal(p_win),
        leader=leader.value,
        ask_win=Decimal(ask_win),
        net_edge=Decimal(net_edge),
    )


def position(token: str, size: str = "10") -> Position:
    return Position(
        round_no=1782480000, token_id=token, size=Decimal(size), avg_price=Decimal("0.9")
    )


# ---------- helpers ----------


class TestBookHelpers:
    def test_best_bid_picks_highest(self) -> None:
        assert best_bid(ob("t", bids=["0.80", "0.85", "0.70"])) == Decimal("0.85")

    def test_best_bid_none_when_empty(self) -> None:
        assert best_bid(ob("t")) is None

    def test_book_depth_sums_all_levels(self) -> None:
        assert book_depth(ob("t", asks=["0.9"], bids=["0.1"])) == Decimal("200")

    def test_flip_ratio_basic(self) -> None:
        held = ob("h", asks=["0.5"])  # depth 100
        opp = ob("o", asks=["0.5"], bids=["0.4"])  # depth 200
        assert flip_ratio(held, opp) == Decimal("200") / Decimal("300")

    def test_flip_ratio_zero_when_no_depth(self) -> None:
        assert flip_ratio(ob("h"), ob("o")) == Decimal("0")


# ---------- entry branch ----------


class TestEntry:
    def test_enter_when_all_filters_pass(self) -> None:
        strat = Strategy(params())
        out = strat.on_tick(sig(), market(up_asks=["0.90"]), None)
        assert len(out) == 1
        dec = out[0]
        assert isinstance(dec, EnterOrder)
        assert dec.token_id == UP_TOKEN
        assert dec.outcome == "UP"
        assert dec.side == "BUY"
        assert dec.price == Decimal("0.90")
        assert dec.order_type == "FOK"

    def test_enter_down_leader(self) -> None:
        strat = Strategy(params())
        out = strat.on_tick(
            sig(delta="-100", leader=Outcome.DOWN, ask_win="0.88"),
            market(down_asks=["0.88"]),
            None,
        )
        assert isinstance(out[0], EnterOrder)
        assert out[0].token_id == DOWN_TOKEN
        assert out[0].outcome == "DOWN"

    def test_noop_when_too_early(self) -> None:
        strat = Strategy(params())
        out = strat.on_tick(sig(time_left=120.0), market(up_asks=["0.90"]), None)
        assert isinstance(out[0], NoOp)
        assert out[0].reason == "time_left>t_entry"

    def test_noop_when_delta_below_threshold(self) -> None:
        strat = Strategy(params(delta_threshold=Decimal("200")))
        out = strat.on_tick(sig(delta="100"), market(up_asks=["0.90"]), None)
        assert isinstance(out[0], NoOp)
        assert out[0].reason == "abs_delta<threshold"

    def test_noop_when_ask_below_min_price(self) -> None:
        strat = Strategy(params())
        out = strat.on_tick(sig(ask_win="0.70"), market(up_asks=["0.70"]), None)
        assert isinstance(out[0], NoOp)
        assert out[0].reason == "ask<min_price"

    def test_noop_when_ask_above_max_price(self) -> None:
        # anti-pattern: NEVER buy > MAX_PRICE
        strat = Strategy(params())
        out = strat.on_tick(sig(ask_win="0.995"), market(up_asks=["0.995"]), None)
        assert isinstance(out[0], NoOp)
        assert out[0].reason == "ask>max_price"

    def test_noop_when_net_edge_below_min(self) -> None:
        strat = Strategy(params())
        out = strat.on_tick(sig(net_edge="0.005"), market(up_asks=["0.90"]), None)
        assert isinstance(out[0], NoOp)
        assert out[0].reason == "net_edge<min_edge"

    def test_noop_when_no_liquidity_ask_is_one(self) -> None:
        # SignalEngine set ask_win=1 saat tanpa likuiditas → > max_price → NoOp.
        strat = Strategy(params())
        out = strat.on_tick(sig(ask_win="1"), market(), None)
        assert isinstance(out[0], NoOp)
        assert out[0].reason == "ask>max_price"

    def test_boundary_net_edge_equal_min_enters(self) -> None:
        strat = Strategy(params(min_edge=Decimal("0.05")))
        out = strat.on_tick(sig(net_edge="0.05"), market(up_asks=["0.90"]), None)
        assert isinstance(out[0], EnterOrder)


# ---------- manage position: hold ----------


class TestHold:
    def test_hold_when_no_trigger(self) -> None:
        strat = Strategy(params())
        # held UP = leader, p_win 0.90 > p_exit; flip low.
        out = strat.on_tick(
            sig(p_win="0.90"),
            market(up_asks=["0.90"], up_bids=["0.88"], down_asks=["0.10"]),
            position(UP_TOKEN),
        )
        assert isinstance(out[0], NoOp)
        assert out[0].reason == "hold"

    def test_noop_when_position_token_unknown(self) -> None:
        strat = Strategy(params())
        out = strat.on_tick(sig(), market(up_asks=["0.9"]), position("other-token"))
        assert isinstance(out[0], NoOp)
        assert out[0].reason == "position_token_not_in_book"


# ---------- manage position: hedge ----------


class TestHedge:
    def test_hedge_when_p_win_held_below_exit(self) -> None:
        # Held UP tapi sekarang DOWN memimpin → p_win_held = 1-0.85 = 0.15 < 0.65.
        strat = Strategy(params())
        out = strat.on_tick(
            sig(delta="-100", leader=Outcome.DOWN, p_win="0.85"),
            market(up_bids=["0.10"], down_asks=["0.85"]),
            position(UP_TOKEN),
        )
        dec = out[0]
        assert isinstance(dec, Hedge)
        assert dec.token_id == DOWN_TOKEN
        assert dec.outcome == "DOWN"
        assert dec.side == "BUY"
        assert dec.price == Decimal("0.85")
        assert dec.hedge_fraction == Decimal("0.5")
        assert dec.reason == "hedge:p_win<p_exit"

    def test_hedge_when_book_flips(self) -> None:
        # p_win_held tinggi (tak trigger exit) tapi depth lawan dominan → flip.
        strat = Strategy(params())
        mkt = MarketBook(
            up=ob(UP_TOKEN, bids=["0.50"]),  # held depth 100
            down=OrderBook(
                token_id=DOWN_TOKEN,
                ts=TS,
                bids=[BookLevel(Decimal("0.49"), Decimal("2000"))],
                asks=[BookLevel(Decimal("0.51"), Decimal("2000"))],
            ),  # opp depth 4000 → flip ≈ 0.975
        )
        out = strat.on_tick(sig(p_win="0.95", leader=Outcome.UP), mkt, position(UP_TOKEN))
        dec = out[0]
        assert isinstance(dec, Hedge)
        assert dec.reason == "hedge:book_flip"


# ---------- manage position: exit ----------


class TestExit:
    def test_exit_when_hedge_unavailable_but_bid_exists(self) -> None:
        # Trigger exit (p_win_held low). Sisi lawan tanpa ask → tak bisa hedge.
        # Held UP punya bid → Exit (jual).
        strat = Strategy(params())
        out = strat.on_tick(
            sig(delta="-100", leader=Outcome.DOWN, p_win="0.90"),
            market(up_bids=["0.20"]),  # down tanpa ask
            position(UP_TOKEN),
        )
        dec = out[0]
        assert isinstance(dec, Exit)
        assert dec.token_id == UP_TOKEN
        assert dec.outcome == "UP"
        assert dec.side == "SELL"
        assert dec.price == Decimal("0.20")

    def test_exit_when_opp_ask_above_max_price(self) -> None:
        # Hedge ask lawan > MAX_PRICE → jangan beli > MAX_PRICE → Exit via bid.
        strat = Strategy(params())
        out = strat.on_tick(
            sig(delta="-100", leader=Outcome.DOWN, p_win="0.95"),
            market(up_bids=["0.05"], down_asks=["0.995"]),
            position(UP_TOKEN),
        )
        assert isinstance(out[0], Exit)


# ---------- manage position: no liquidity ----------


class TestTriggerNoLiquidity:
    def test_noop_when_trigger_but_no_liquidity(self) -> None:
        strat = Strategy(params())
        out = strat.on_tick(
            sig(delta="-100", leader=Outcome.DOWN, p_win="0.90"),
            market(),  # tak ada ask lawan & tak ada bid held
            position(UP_TOKEN),
        )
        dec = out[0]
        assert isinstance(dec, NoOp)
        assert dec.reason == "trigger_no_liquidity:p_win<p_exit"


# ---------- never-fade invariant ----------


class TestNeverFade:
    def test_entry_always_on_leader_side(self) -> None:
        strat = Strategy(params())
        # DOWN memimpin → entry HARUS sisi DOWN, tak pernah UP (fade).
        out = strat.on_tick(
            sig(delta="-100", leader=Outcome.DOWN, ask_win="0.90"),
            market(up_asks=["0.10"], down_asks=["0.90"]),
            None,
        )
        assert isinstance(out[0], EnterOrder)
        assert out[0].outcome == "DOWN"


class TestStrategyParamsValidation:
    def test_min_gt_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_price"):
            params(min_price=Decimal("0.99"), max_price=Decimal("0.80"))

    def test_negative_threshold_rejected(self) -> None:
        with pytest.raises(ValueError, match="delta_threshold"):
            params(delta_threshold=Decimal("-1"))
