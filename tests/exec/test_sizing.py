"""Unit tests for btcbot.exec.sizing."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from btcbot.config.settings import Settings
from btcbot.domain.models import Signal
from btcbot.exec.sizing import (
    BindingCap,
    SizingLimits,
    active_bankroll,
    compute_size,
    round_to_tick,
    size,
)


def _limits(  # noqa: PLR0913
    *,
    kelly_fraction: str = "0.25",
    max_notional_round: str = "5",
    max_bankroll_fraction: str = "0.02",
    fill_safety: str = "0.8",
    min_edge: str = "0.01",
    max_price: str = "0.99",
    min_order_size: str = "0",
    tick_size: str = "0",
) -> SizingLimits:
    return SizingLimits(
        kelly_fraction=Decimal(kelly_fraction),
        max_notional_round=Decimal(max_notional_round),
        max_bankroll_fraction=Decimal(max_bankroll_fraction),
        fill_safety=Decimal(fill_safety),
        min_edge=Decimal(min_edge),
        max_price=Decimal(max_price),
        min_order_size=Decimal(min_order_size),
        tick_size=Decimal(tick_size),
    )


def _signal(*, p_win: str = "0.9", ask_win: str = "0.9", net_edge: str = "0.05") -> Signal:
    return Signal(
        round_no=1782480000,
        ts=datetime(2026, 6, 26, 13, 19, 45, tzinfo=UTC),
        price_now=Decimal("65100"),
        delta=Decimal("100"),
        time_left_sec=15.0,
        p_win=Decimal(p_win),
        leader="UP",
        ask_win=Decimal(ask_win),
        net_edge=Decimal(net_edge),
    )


# Kombinasi untuk uji invariant property-style (modul-level → hindari RUF012).
_SIZE_CASES = [
    (p, a, e, bk, dp)
    for p in ("0.55", "0.75", "0.95", "0.99")
    for a in ("0.80", "0.90", "0.96", "0.99")
    for e in ("-0.1", "0.0", "0.02", "0.08", "0.2")
    for bk in ("50", "200", "1000")
    for dp in ("0.05", "5", "1000")
]


class TestBindingCaps:
    def test_kelly_is_binding(self) -> None:
        # kelly_fraction kecil → size_kelly terkecil.
        res = compute_size(
            p_win=Decimal("0.6"),
            ask=Decimal("0.5"),
            fair_price=Decimal("0.6"),
            bankroll=Decimal("200"),
            depth_available=Decimal("1000"),
            limits=_limits(kelly_fraction="0.01", max_bankroll_fraction="0.5"),
        )
        assert res.binding_cap is BindingCap.KELLY
        assert res.size == Decimal("0.01") * Decimal("0.8") * Decimal("200") / Decimal("0.5")

    def test_notional_is_binding(self) -> None:
        res = compute_size(
            p_win=Decimal("0.9"),
            ask=Decimal("0.5"),
            fair_price=Decimal("0.9"),
            bankroll=Decimal("200"),
            depth_available=Decimal("1000"),
            limits=_limits(max_notional_round="1", max_bankroll_fraction="0.5"),
        )
        assert res.binding_cap is BindingCap.NOTIONAL
        assert res.size == Decimal("1") / Decimal("0.5")  # = 2 share

    def test_bankroll_fraction_is_binding_concrete_example(self) -> None:
        # bankroll=200, ask=0.96, MAX_BANKROLL_FRACTION=0.02, MAX_NOTIONAL_ROUND=5
        # → cap notional efektif = min(5, 4) = $4 (≈4.17 share)
        res = compute_size(
            p_win=Decimal("0.99"),
            ask=Decimal("0.96"),
            fair_price=Decimal("0.99"),
            bankroll=Decimal("200"),
            depth_available=Decimal("1000"),
            limits=_limits(),
        )
        assert res.binding_cap is BindingCap.BANKROLL_FRACTION
        assert res.size == Decimal("4") / Decimal("0.96")  # ≈ 4.1667 share
        assert abs(res.notional - Decimal("4")) < Decimal("0.0001")

    def test_depth_is_binding(self) -> None:
        res = compute_size(
            p_win=Decimal("0.9"),
            ask=Decimal("0.5"),
            fair_price=Decimal("0.9"),
            bankroll=Decimal("200"),
            depth_available=Decimal("1"),
            limits=_limits(max_notional_round="5", max_bankroll_fraction="0.5"),
        )
        assert res.binding_cap is BindingCap.DEPTH
        assert res.size == Decimal("1") * Decimal("0.8")  # = 0.8 share


class TestEdgeGate:
    def test_edge_below_min_no_entry(self) -> None:
        res = compute_size(
            p_win=Decimal("0.9"),
            ask=Decimal("0.5"),
            fair_price=Decimal("0.505"),  # edge = 0.005 <= MIN_EDGE 0.01
            bankroll=Decimal("200"),
            depth_available=Decimal("1000"),
            limits=_limits(),
        )
        assert res.size == Decimal("0")
        assert res.binding_cap is BindingCap.NONE

    def test_edge_equal_min_no_entry(self) -> None:
        res = compute_size(
            p_win=Decimal("0.9"),
            ask=Decimal("0.5"),
            fair_price=Decimal("0.51"),  # edge = 0.01 == MIN_EDGE
            bankroll=Decimal("200"),
            depth_available=Decimal("1000"),
            limits=_limits(),
        )
        assert res.size == Decimal("0")


class TestInvariants:
    def test_never_fade_kelly_floored_to_zero(self) -> None:
        # p_win rendah relatif ask → Kelly mentah negatif → di-floor ke 0.
        res = compute_size(
            p_win=Decimal("0.3"),
            ask=Decimal("0.5"),
            fair_price=Decimal("0.9"),  # edge lolos, tapi Kelly = 0
            bankroll=Decimal("200"),
            depth_available=Decimal("1000"),
            limits=_limits(),
        )
        assert res.size == Decimal("0")

    def test_above_max_price_no_entry(self) -> None:
        res = compute_size(
            p_win=Decimal("0.99"),
            ask=Decimal("0.995"),  # > max_price 0.99
            fair_price=Decimal("0.99"),
            bankroll=Decimal("200"),
            depth_available=Decimal("1000"),
            limits=_limits(),
        )
        assert res.size == Decimal("0")

    def test_zero_bankroll_no_entry(self) -> None:
        res = compute_size(
            p_win=Decimal("0.9"),
            ask=Decimal("0.5"),
            fair_price=Decimal("0.9"),
            bankroll=Decimal("0"),
            depth_available=Decimal("1000"),
            limits=_limits(),
        )
        assert res.size == Decimal("0")

    def test_below_min_order_size_no_entry(self) -> None:
        res = compute_size(
            p_win=Decimal("0.9"),
            ask=Decimal("0.5"),
            fair_price=Decimal("0.9"),
            bankroll=Decimal("200"),
            depth_available=Decimal("0.1"),  # cap_depth = 0.08 share
            limits=_limits(max_bankroll_fraction="0.5", min_order_size="1"),
        )
        assert res.size == Decimal("0")

    def test_size_never_negative(self) -> None:
        res = compute_size(
            p_win=Decimal("0.95"),
            ask=Decimal("0.9"),
            fair_price=Decimal("0.95"),
            bankroll=Decimal("200"),
            depth_available=Decimal("1000"),
            limits=_limits(),
        )
        assert res.size >= Decimal("0")


class TestActiveBankroll:
    def test_paper_uses_starting_balance(self) -> None:
        s = Settings(paper_trading=True, paper_starting_balance=Decimal("200"))
        assert active_bankroll(s) == Decimal("200")

    def test_paper_uses_running_balance_when_given(self) -> None:
        s = Settings(paper_trading=True, paper_starting_balance=Decimal("200"))
        assert active_bankroll(s, Decimal("175.5")) == Decimal("175.5")

    def test_live_not_implemented(self) -> None:
        s = Settings(paper_trading=False)
        with pytest.raises(NotImplementedError):
            active_bankroll(s)

    def test_from_settings(self) -> None:
        s = Settings(
            kelly_fraction=Decimal("0.25"),
            max_notional_round=Decimal("5"),
            max_bankroll_fraction=Decimal("0.02"),
            fill_safety=Decimal("0.8"),
            min_edge=Decimal("0.01"),
            max_price=Decimal("0.99"),
        )
        limits = SizingLimits.from_settings(s)
        assert limits.kelly_fraction == Decimal("0.25")
        assert limits.max_bankroll_fraction == Decimal("0.02")
        assert limits.fill_safety == Decimal("0.8")


class TestRoundToTick:
    def test_floors_to_tick(self) -> None:
        assert round_to_tick(Decimal("4.1667"), Decimal("0.01")) == Decimal("4.16")

    def test_floors_to_coarse_tick(self) -> None:
        assert round_to_tick(Decimal("4.1667"), Decimal("0.1")) == Decimal("4.1")

    def test_exact_multiple_unchanged(self) -> None:
        assert round_to_tick(Decimal("5"), Decimal("0.5")) == Decimal("5")

    def test_zero_tick_no_rounding(self) -> None:
        assert round_to_tick(Decimal("4.1667"), Decimal("0")) == Decimal("4.1667")

    def test_never_rounds_up(self) -> None:
        assert round_to_tick(Decimal("4.999"), Decimal("1")) == Decimal("4")


class TestSizeFromSignal:
    def test_uses_net_edge_not_recomputing_fee(self) -> None:
        # net_edge sudah net-of-fee; sizer pakai apa adanya (gate lolos).
        s = _signal(p_win="0.9", ask_win="0.5", net_edge="0.05")
        out = size(s, Decimal("200"), Decimal("1000"), _limits(max_bankroll_fraction="0.5"))
        assert out > Decimal("0")

    def test_zero_when_net_edge_below_min(self) -> None:
        s = _signal(net_edge="0.005")  # <= MIN_EDGE 0.01
        out = size(s, Decimal("200"), Decimal("1000"), _limits())
        assert out == Decimal("0")

    def test_zero_when_net_edge_equal_min(self) -> None:
        s = _signal(net_edge="0.01")
        out = size(s, Decimal("200"), Decimal("1000"), _limits())
        assert out == Decimal("0")

    def test_zero_when_net_edge_negative(self) -> None:
        s = _signal(net_edge="-0.2")
        out = size(s, Decimal("200"), Decimal("1000"), _limits())
        assert out == Decimal("0")

    def test_rounds_to_tick(self) -> None:
        # bankroll-fraction binding: $4 / 0.96 ≈ 4.1667 → tick 0.01 → 4.16.
        s = _signal(p_win="0.99", ask_win="0.96", net_edge="0.05")
        out = size(s, Decimal("200"), Decimal("1000"), _limits(tick_size="0.01"))
        assert out == Decimal("4.16")

    def test_below_min_order_after_tick_is_zero(self) -> None:
        s = _signal(p_win="0.9", ask_win="0.5", net_edge="0.05")
        # depth cap 0.08 share; min_order_size 1 → 0.
        out = size(
            s,
            Decimal("200"),
            Decimal("0.1"),
            _limits(max_bankroll_fraction="0.5", min_order_size="1"),
        )
        assert out == Decimal("0")

    def test_never_fade_zero_kelly(self) -> None:
        # p_win rendah relatif ask → Kelly 0 (meski net_edge lolos gate).
        s = _signal(p_win="0.3", ask_win="0.5", net_edge="0.05")
        out = size(s, Decimal("200"), Decimal("1000"), _limits())
        assert out == Decimal("0")


class TestSizeInvariants:
    """Property-style: ukuran tak pernah melampaui cap mana pun, depth*FILL_SAFETY,
    dan = 0 saat net_edge <= MIN_EDGE. Diuji lintas banyak kombinasi."""

    @pytest.mark.parametrize(("p_win", "ask", "net_edge", "bankroll", "depth"), _SIZE_CASES)
    def test_invariants(
        self,
        p_win: str,
        ask: str,
        net_edge: str,
        bankroll: str,
        depth: str,
    ) -> None:
        limits = _limits()
        s = _signal(p_win=p_win, ask_win=ask, net_edge=net_edge)
        bk = Decimal(bankroll)
        dp = Decimal(depth)
        out = size(s, bk, dp, limits)

        # Selalu non-negatif.
        assert out >= Decimal("0")

        if Decimal(net_edge) <= limits.min_edge:
            assert out == Decimal("0")
            return

        notional = out * Decimal(ask)
        # Tidak pernah melampaui cap notional absolut.
        assert notional <= limits.max_notional_round + Decimal("1e-9")
        # Tidak pernah melampaui cap % bankroll.
        assert notional <= bk * limits.max_bankroll_fraction + Decimal("1e-9")
        # Tidak pernah melampaui depth * FILL_SAFETY.
        assert out <= dp * limits.fill_safety + Decimal("1e-9")
