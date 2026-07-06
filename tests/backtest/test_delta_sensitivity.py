"""Test delta_threshold parameter sensitivity (TEMUAN 1 regression).

Verifies that different delta_threshold values produce different entry counts
on synthetic data with mixed small/large deltas. This test proves the wiring
from --delta-grid CLI argument → Strategy filter is functioning correctly.
"""

from datetime import UTC, datetime
from decimal import Decimal

from btcbot.backtest.replay import ReplayConfig, ReplayEngine, ReplayTick
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus


def _make_round() -> Round:
    """Round with 5-minute window."""
    return Round(
        condition_id="test",
        round_no=1000,
        token_id_up="up_token",
        token_id_down="down_token",
        window_start=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 10, 5, tzinfo=UTC),
        start_price=Decimal("50000"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("1"),
        status=RoundStatus.ACTIVE,
        resolved_outcome=Outcome.UP,
    )


def _book(token: str, ask_price: str) -> OrderBook:
    """Order book with single ask level."""
    return OrderBook(
        token_id=token,
        ts=datetime(2026, 1, 1, 10, 4, 30, tzinfo=UTC),
        bids=[],
        asks=[BookLevel(price=Decimal(ask_price), size=Decimal("100"))],
    )


class TestDeltaThresholdSensitivity:
    """Verify delta_threshold parameter affects entry decisions."""

    def test_small_delta_filtered_by_high_threshold(self) -> None:
        """Delta between thresholds should pass low threshold, fail high threshold."""
        rnd = _make_round()
        
        # Create tick with delta=25 (intermediate value)
        # start_price=50000, price_now=50025 → delta=+25
        tick_small = ReplayTick(
            ts=datetime(2026, 1, 1, 10, 4, 30, tzinfo=UTC),  # time_left=30s < t_entry=60s
            btc_price=Decimal("50025"),
            book_up=_book("up_token", "0.55"),
            book_down=_book("down_token", "0.48"),
        )
        
        # Create tick with delta=100 (large value)
        tick_large = ReplayTick(
            ts=datetime(2026, 1, 1, 10, 4, 30, tzinfo=UTC),
            btc_price=Decimal("50100"),
            book_up=_book("up_token", "0.55"),
            book_down=_book("down_token", "0.48"),
        )
        
        # Config with delta_threshold=20 (should pass delta=25)
        cfg_low = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=60,
            delta_threshold=Decimal("20"),
            min_edge=Decimal("-1"),  # Allow any edge (disable edge filter)
        )
        
        # Config with delta_threshold=50 (should filter delta=25)
        cfg_high = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=60,
            delta_threshold=Decimal("50"),
            min_edge=Decimal("-1"),
        )
        
        # Test small delta (25)
        summary_low_small = ReplayEngine(cfg_low).run([(rnd, [tick_small])])
        summary_high_small = ReplayEngine(cfg_high).run([(rnd, [tick_small])])
        
        assert summary_low_small.rounds_entered == 1, \
            "delta=25 should PASS threshold=20 (25 >= 20)"
        assert summary_high_small.rounds_entered == 0, \
            "delta=25 should FAIL threshold=50 (25 < 50)"
        
        # Test large delta (100)
        summary_low_large = ReplayEngine(cfg_low).run([(rnd, [tick_large])])
        summary_high_large = ReplayEngine(cfg_high).run([(rnd, [tick_large])])
        
        assert summary_low_large.rounds_entered == 1, \
            "delta=100 should PASS threshold=20"
        assert summary_high_large.rounds_entered == 1, \
            "delta=100 should PASS threshold=50"

    def test_grid_delta_affects_entered_count(self) -> None:
        """Grid with different delta values should produce different entered counts."""
        rnd = _make_round()
        
        # Create ticks with range of deltas: 0, 30, 60, 90
        ticks = [
            ReplayTick(
                ts=datetime(2026, 1, 1, 10, 4, i, tzinfo=UTC),
                btc_price=Decimal("50000") + Decimal(str(30 * i)),
                book_up=_book("up_token", "0.55"),
                book_down=_book("down_token", "0.48"),
            )
            for i in range(4)
        ]
        
        cfg_delta_20 = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=120,  # All ticks within entry window
            delta_threshold=Decimal("20"),
            min_edge=Decimal("-1"),
        )
        
        cfg_delta_50 = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=120,
            delta_threshold=Decimal("50"),
            min_edge=Decimal("-1"),
        )
        
        summary_20 = ReplayEngine(cfg_delta_20).run([(rnd, ticks)])
        summary_50 = ReplayEngine(cfg_delta_50).run([(rnd, ticks)])
        
        # delta_threshold=20 should allow: 0(no), 30(yes), 60(yes), 90(yes) = 3 entries
        # delta_threshold=50 should allow: 0(no), 30(no), 60(yes), 90(yes) = 2 entries
        assert summary_20.rounds_entered == 3, \
            f"threshold=20 should enter 3 ticks (delta 30,60,90), got {summary_20.rounds_entered}"
        assert summary_50.rounds_entered == 2, \
            f"threshold=50 should enter 2 ticks (delta 60,90), got {summary_50.rounds_entered}"
        
        # Key assertion: different thresholds produce different results
        assert summary_20.rounds_entered > summary_50.rounds_entered, \
            "Lower delta_threshold should produce MORE entries than higher threshold"

    def test_all_deltas_above_threshold_gives_same_result(self) -> None:
        """If ALL deltas > all thresholds, grid results should be identical (DATA issue).
        
        This test demonstrates the scenario from TEMUAN 1: if the dataset has no
        small deltas, then grid sweep will produce identical results (not a bug).
        """
        rnd = _make_round()
        
        # All ticks have large delta (> 100)
        ticks = [
            ReplayTick(
                ts=datetime(2026, 1, 1, 10, 4, i, tzinfo=UTC),
                btc_price=Decimal("50000") + Decimal(str(150 + 50 * i)),  # delta: 150, 200, 250, 300
                book_up=_book("up_token", "0.55"),
                book_down=_book("down_token", "0.48"),
            )
            for i in range(4)
        ]
        
        # Try 3 different thresholds, all < min(delta)
        cfg_delta_20 = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=120,
            delta_threshold=Decimal("20"),
            min_edge=Decimal("-1"),
        )
        
        cfg_delta_50 = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=120,
            delta_threshold=Decimal("50"),
            min_edge=Decimal("-1"),
        )
        
        cfg_delta_100 = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=120,
            delta_threshold=Decimal("100"),
            min_edge=Decimal("-1"),
        )
        
        summary_20 = ReplayEngine(cfg_delta_20).run([(rnd, ticks)])
        summary_50 = ReplayEngine(cfg_delta_50).run([(rnd, ticks)])
        summary_100 = ReplayEngine(cfg_delta_100).run([(rnd, ticks)])
        
        # All 3 configs should produce identical results (all deltas pass all thresholds)
        assert summary_20.rounds_entered == summary_50.rounds_entered == summary_100.rounds_entered == 4, \
            "When all deltas > all thresholds, grid results should be identical (data issue, not bug)"
        
        # PnL should also be identical (same entries → same trades)
        assert summary_20.total_pnl == summary_50.total_pnl == summary_100.total_pnl, \
            "Identical entries should produce identical PnL"
