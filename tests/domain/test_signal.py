"""Unit tests for btcbot.domain.signal (SignalEngine + edge math).

Numerik & deterministik (docs/05 §5.1-5.3). Memverifikasi:
- Φ (normal_cdf): z besar → p_win → 1.
- net_edge turun saat fee / slippage naik (fee TIDAK nol).
- leader & ask_win benar; tanpa likuiditas → ask_win=1.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from btcbot.domain.fees import ProportionalTakerFee, ZeroFee
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus
from btcbot.domain.signal import SignalEngine, best_ask, normal_cdf

WINDOW_END = datetime(2026, 6, 26, 13, 20, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)
START_PRICE = Decimal("65000")


def make_round() -> Round:
    return Round(
        condition_id="0xcond",
        round_no=1782480000,
        token_id_up="up",
        token_id_down="down",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        start_price=START_PRICE,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        status=RoundStatus.ACTIVE,
    )


def book(token_id: str, *asks: str) -> OrderBook:
    """Bangun OrderBook dengan daftar harga ask (string)."""
    return OrderBook(
        token_id=token_id,
        ts=WINDOW_START,
        bids=[BookLevel(Decimal("0.10"), Decimal("100"))],
        asks=[BookLevel(Decimal(p), Decimal("100")) for p in asks],
    )


class TestNormalCdf:
    def test_zero_is_half(self) -> None:
        assert normal_cdf(0) == Decimal("0.5")

    def test_large_positive_approaches_one(self) -> None:
        assert normal_cdf(8.0) > Decimal("0.999999")

    def test_large_negative_approaches_zero(self) -> None:
        assert normal_cdf(-8.0) < Decimal("0.000001")

    def test_symmetry(self) -> None:
        assert normal_cdf(1.5) + normal_cdf(-1.5) == pytest.approx(Decimal("1.0"))


class TestBestAsk:
    def test_picks_lowest(self) -> None:
        assert best_ask(book("up", "0.95", "0.90", "0.97")) == Decimal("0.90")

    def test_none_for_empty(self) -> None:
        assert best_ask(book("up")) is None

    def test_none_for_none(self) -> None:
        assert best_ask(None) is None


class TestComputeLeader:
    def test_up_leads_when_delta_positive(self) -> None:
        engine = SignalEngine()
        sig = engine.compute(
            make_round(),
            Decimal("65100"),
            WINDOW_START,
            Decimal("10"),
            book_up=book("up", "0.90"),
            book_down=book("down", "0.10"),
        )
        assert sig.leader == Outcome.UP.value
        assert sig.delta == Decimal("100")
        assert sig.ask_win == Decimal("0.90")

    def test_down_leads_when_delta_negative(self) -> None:
        engine = SignalEngine()
        sig = engine.compute(
            make_round(),
            Decimal("64900"),
            WINDOW_START,
            Decimal("10"),
            book_up=book("up", "0.10"),
            book_down=book("down", "0.88"),
        )
        assert sig.leader == Outcome.DOWN.value
        assert sig.delta == Decimal("-100")
        assert sig.ask_win == Decimal("0.88")

    def test_delta_zero_defaults_to_down(self) -> None:
        engine = SignalEngine()
        sig = engine.compute(
            make_round(),
            START_PRICE,
            WINDOW_START,
            Decimal("10"),
            book_down=book("down", "0.50"),
        )
        assert sig.leader == Outcome.DOWN.value
        assert sig.p_win == Decimal("0.5")  # z=0 → Φ(0)=0.5


class TestComputePWin:
    def test_large_z_drives_p_win_to_one(self) -> None:
        # time_left -> 0 (now == window_end) → sigma_left=0 → z besar → p_win≈1.
        engine = SignalEngine()
        sig = engine.compute(
            make_round(),
            Decimal("65100"),
            WINDOW_END,
            Decimal("10"),
            book_up=book("up", "0.95"),
        )
        assert sig.time_left_sec == 0.0
        assert sig.p_win > Decimal("0.999999")

    def test_p_win_uses_leader_magnitude(self) -> None:
        # DOWN memimpin: p_win = Φ(|z|) > 0.5 (bukan Φ(z) negatif).
        engine = SignalEngine()
        sig = engine.compute(
            make_round(),
            Decimal("64500"),
            WINDOW_END - timedelta(seconds=10),
            Decimal("5"),
            book_down=book("down", "0.90"),
        )
        assert sig.leader == Outcome.DOWN.value
        assert sig.p_win > Decimal("0.5")


class TestComputeNetEdge:
    def _sig_with(self, fee_rate: str, slippage: str) -> Decimal:
        engine = SignalEngine(fee_model=ProportionalTakerFee(Decimal(fee_rate)))
        sig = engine.compute(
            make_round(),
            Decimal("65100"),
            WINDOW_END - timedelta(seconds=100),
            Decimal("10"),
            book_up=book("up", "0.80"),
            expected_slippage=Decimal(slippage),
        )
        return sig.net_edge

    def test_net_edge_formula(self) -> None:
        engine = SignalEngine(fee_model=ProportionalTakerFee(Decimal("0.07")))
        sig = engine.compute(
            make_round(),
            Decimal("65100"),
            WINDOW_END - timedelta(seconds=100),
            Decimal("10"),
            book_up=book("up", "0.80"),
            expected_slippage=Decimal("0.01"),
        )
        # net_edge = p_win - ask_win - fee(=0.07*0.80) - slippage
        expected = sig.p_win - Decimal("0.80") - Decimal("0.056") - Decimal("0.01")
        assert sig.net_edge == expected

    def test_net_edge_decreases_when_fee_rises(self) -> None:
        low_fee = self._sig_with("0.01", "0")
        high_fee = self._sig_with("0.07", "0")
        assert high_fee < low_fee

    def test_net_edge_decreases_when_slippage_rises(self) -> None:
        low_slip = self._sig_with("0.07", "0")
        high_slip = self._sig_with("0.07", "0.05")
        assert high_slip < low_slip

    def test_zero_fee_gives_highest_edge(self) -> None:
        engine_zero = SignalEngine(fee_model=ZeroFee())
        engine_fee = SignalEngine(fee_model=ProportionalTakerFee(Decimal("0.07")))
        rnd = make_round()
        now = WINDOW_END - timedelta(seconds=100)
        edge_zero = engine_zero.compute(
            rnd, Decimal("65100"), now, Decimal("10"), book_up=book("up", "0.80")
        ).net_edge
        edge_fee = engine_fee.compute(
            rnd, Decimal("65100"), now, Decimal("10"), book_up=book("up", "0.80")
        ).net_edge
        assert edge_zero > edge_fee


class TestComputeEdgeCases:
    def test_missing_leader_book_sets_ask_one(self) -> None:
        engine = SignalEngine()
        sig = engine.compute(
            make_round(),
            Decimal("65100"),
            WINDOW_START,
            Decimal("10"),
            book_up=None,
        )
        assert sig.ask_win == Decimal("1")
        assert sig.net_edge <= Decimal("0")

    def test_empty_asks_sets_ask_one(self) -> None:
        engine = SignalEngine()
        sig = engine.compute(
            make_round(),
            Decimal("65100"),
            WINDOW_START,
            Decimal("10"),
            book_up=book("up"),  # tidak ada ask
        )
        assert sig.ask_win == Decimal("1")

    def test_deterministic(self) -> None:
        engine = SignalEngine()
        rnd = make_round()
        now = WINDOW_END - timedelta(seconds=30)
        first = engine.compute(rnd, Decimal("65123"), now, Decimal("8"), book_up=book("up", "0.91"))
        second = engine.compute(
            rnd, Decimal("65123"), now, Decimal("8"), book_up=book("up", "0.91")
        )
        assert first == second

    def test_signal_fields_populated(self) -> None:
        engine = SignalEngine()
        now = WINDOW_END - timedelta(seconds=45)
        sig = engine.compute(
            make_round(),
            Decimal("65100"),
            now,
            Decimal("10"),
            book_up=book("up", "0.90"),
        )
        assert sig.round_no == 1782480000
        assert sig.ts == now
        assert sig.price_now == Decimal("65100")
        assert sig.time_left_sec == pytest.approx(45.0)
