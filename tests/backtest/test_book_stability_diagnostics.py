"""tests/backtest/test_book_stability_diagnostics.py — Tests for book stability diagnostics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from btcbot.backtest.book_stability_diagnostics import (
    BookStabilityMetrics,
    BucketStats,
    StabilityThresholds,
    _compute_stability_metrics,
)
from btcbot.backtest.replay import RoundResult
from btcbot.data.store import BookSnapshot

_ZERO = Decimal("0")


def _make_snapshot(
    token_id: str,
    ts: datetime,
    best_bid: Decimal | None,
    best_ask: Decimal | None,
) -> BookSnapshot:
    """Helper to create BookSnapshot."""
    return BookSnapshot(
        round_no=1234567890,
        token_id=token_id,
        ts=ts,
        best_bid=best_bid,
        best_ask=best_ask,
        bid_depth=Decimal("10"),
        ask_depth=Decimal("10"),
        gap=False,
        raw=None,
        mode="test",
    )


def _make_result(
    round_no: int,
    side_taken: str,
    entry_price: Decimal,
    pnl: Decimal,
) -> RoundResult:
    """Helper to create RoundResult."""
    return RoundResult(
        round_no=round_no,
        side_taken=side_taken,
        entry_price=entry_price,
        size=Decimal("5"),
        hedge_cost=_ZERO,
        settled=Decimal("5") if pnl > _ZERO else _ZERO,
        pnl=pnl,
        balance_after=Decimal("500") + pnl,
    )


class TestBucketStats:
    """Tests for BucketStats."""

    def test_win_rate(self) -> None:
        stats = BucketStats(entries=10, wins=8, losses=2)
        assert stats.win_rate() == 80.0

    def test_win_rate_zero_entries(self) -> None:
        stats = BucketStats()
        assert stats.win_rate() == 0.0

    def test_avg_pnl(self) -> None:
        stats = BucketStats(entries=4, pnl=Decimal("12.0"))
        assert stats.avg_pnl() == Decimal("3.0")



class TestComputeStabilityMetrics:
    """Tests for _compute_stability_metrics function."""

    def test_stable_winning_trade_no_warning(self) -> None:
        """Stable trade with no book instability should have no warnings."""
        window_end = datetime(2026, 7, 8, 14, 15, 0, tzinfo=timezone.utc)
        entry_ts = window_end - timedelta(seconds=60)
        result = _make_result(1234567890, "UP", Decimal("0.96"), Decimal("4.5"))

        # Stable book: leader stays high, opposite stays low
        snapshots = [
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=10), Decimal("0.94"), Decimal("0.96")),
            _make_snapshot("token-down-456", entry_ts + timedelta(seconds=10), Decimal("0.04"), Decimal("0.06")),
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=30), Decimal("0.95"), Decimal("0.97")),
            _make_snapshot("token-down-456", entry_ts + timedelta(seconds=30), Decimal("0.03"), Decimal("0.05")),
        ]

        thresholds = StabilityThresholds()
        metrics = _compute_stability_metrics(
            result, window_end, snapshots, thresholds,
            entry_ts, "token-up-123", "token-down-456"
        )

        assert metrics.round_no == 1234567890
        assert metrics.side_taken == "UP"
        assert metrics.result == "WIN"
        assert metrics.entry_price == Decimal("0.96")
        assert metrics.book_flip_warning is False
        assert metrics.leader_bid_below_0_90 is False
        assert metrics.opposite_bid_above_0_10 is False
        assert metrics.first_instability_ts is None

    def test_leader_bid_drops_below_threshold(self) -> None:
        """Leader bid drops below threshold should trigger warning."""
        window_end = datetime(2026, 7, 8, 14, 15, 0, tzinfo=timezone.utc)
        entry_ts = window_end - timedelta(seconds=60)
        result = _make_result(1234567890, "UP", Decimal("0.96"), Decimal("-5.0"))

        # Leader bid crashes
        snapshots = [
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=5), Decimal("0.95"), Decimal("0.96")),
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=10), Decimal("0.88"), Decimal("0.90")),  # Crash!
            _make_snapshot("token-down-456", entry_ts + timedelta(seconds=10), Decimal("0.08"), Decimal("0.10")),
        ]

        thresholds = StabilityThresholds(leader_bid_warn=Decimal("0.90"))
        metrics = _compute_stability_metrics(
            result, window_end, snapshots, thresholds,
            entry_ts, "token-up-123", "token-down-456"
        )

        assert metrics.result == "LOSS"
        assert metrics.leader_bid_below_0_90 is True
        assert metrics.book_flip_warning is True
        assert metrics.min_leader_bid_after_entry == Decimal("0.88")
        assert metrics.first_instability_ts is not None

    def test_opposite_bid_spikes_above_threshold(self) -> None:
        """Opposite bid spikes above threshold should trigger warning."""
        window_end = datetime(2026, 7, 8, 14, 15, 0, tzinfo=timezone.utc)
        entry_ts = window_end - timedelta(seconds=60)
        result = _make_result(1234567890, "DOWN", Decimal("0.96"), Decimal("-4.8"))

        # Opposite (UP) bid spikes
        snapshots = [
            _make_snapshot("token-down-456", entry_ts + timedelta(seconds=5), Decimal("0.94"), Decimal("0.96")),
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=10), Decimal("0.12"), Decimal("0.14")),  # Spike!
            _make_snapshot("token-down-456", entry_ts + timedelta(seconds=10), Decimal("0.85"), Decimal("0.88")),
        ]

        thresholds = StabilityThresholds(opposite_bid_warn=Decimal("0.10"))
        metrics = _compute_stability_metrics(
            result, window_end, snapshots, thresholds,
            entry_ts, "token-up-123", "token-down-456"
        )

        assert metrics.result == "LOSS"
        assert metrics.opposite_bid_above_0_10 is True
        assert metrics.book_flip_warning is True
        assert metrics.max_opposite_bid_after_entry == Decimal("0.12")

    def test_leader_ask_drops_below_threshold(self) -> None:
        """Leader ask drops below threshold should trigger warning."""
        window_end = datetime(2026, 7, 8, 14, 15, 0, tzinfo=timezone.utc)
        entry_ts = window_end - timedelta(seconds=60)
        result = _make_result(1234567890, "UP", Decimal("0.96"), Decimal("-5.0"))

        # Leader ask crashes
        snapshots = [
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=5), Decimal("0.94"), Decimal("0.96")),
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=10), Decimal("0.90"), Decimal("0.92")),  # Crash!
            _make_snapshot("token-down-456", entry_ts + timedelta(seconds=10), Decimal("0.06"), Decimal("0.08")),
        ]

        thresholds = StabilityThresholds(leader_ask_warn=Decimal("0.93"))
        metrics = _compute_stability_metrics(
            result, window_end, snapshots, thresholds,
            entry_ts, "token-up-123", "token-down-456"
        )

        assert metrics.result == "LOSS"
        assert metrics.leader_ask_below_0_90 is True
        assert metrics.book_flip_warning is True
        assert metrics.min_leader_ask_after_entry == Decimal("0.92")

    def test_drawdown_triggers_warning(self) -> None:
        """Large drawdown should trigger warning."""
        window_end = datetime(2026, 7, 8, 14, 15, 0, tzinfo=timezone.utc)
        entry_ts = window_end - timedelta(seconds=60)
        result = _make_result(1234567890, "UP", Decimal("0.96"), Decimal("-5.0"))

        # Leader bid drops significantly (drawdown >= 0.06)
        snapshots = [
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=5), Decimal("0.95"), Decimal("0.96")),
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=10), Decimal("0.89"), Decimal("0.91")),  # 0.96 - 0.89 = 0.07
            _make_snapshot("token-down-456", entry_ts + timedelta(seconds=10), Decimal("0.08"), Decimal("0.10")),
        ]

        thresholds = StabilityThresholds(drawdown_warn=Decimal("0.06"))
        metrics = _compute_stability_metrics(
            result, window_end, snapshots, thresholds,
            entry_ts, "token-up-123", "token-down-456"
        )

        assert metrics.result == "LOSS"
        assert metrics.leader_bid_drawdown == Decimal("0.07")
        assert metrics.book_flip_warning is True

    def test_first_instability_is_earliest(self) -> None:
        """first_instability_ts should be the earliest trigger."""
        window_end = datetime(2026, 7, 8, 14, 15, 0, tzinfo=timezone.utc)
        entry_ts = window_end - timedelta(seconds=60)
        result = _make_result(1234567890, "UP", Decimal("0.96"), Decimal("-5.0"))

        # Multiple instabilities at different times
        snapshots = [
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=5), Decimal("0.94"), Decimal("0.96")),
            _make_snapshot("token-down-456", entry_ts + timedelta(seconds=10), Decimal("0.11"), Decimal("0.13")),  # First!
            _make_snapshot("token-up-123", entry_ts + timedelta(seconds=20), Decimal("0.88"), Decimal("0.90")),  # Later
        ]

        thresholds = StabilityThresholds(opposite_bid_warn=Decimal("0.10"), leader_bid_warn=Decimal("0.90"))
        metrics = _compute_stability_metrics(
            result, window_end, snapshots, thresholds,
            entry_ts, "token-up-123", "token-down-456"
        )

        assert metrics.book_flip_warning is True
        assert metrics.first_instability_ts == entry_ts + timedelta(seconds=10)
        assert metrics.seconds_after_entry_to_instability is not None
        assert abs(metrics.seconds_after_entry_to_instability - 10.0) < 0.1  # Exactly 10s after entry

    def test_no_post_entry_snapshots_handled_safely(self) -> None:
        """Empty post_entry_snapshots should be handled without crashing."""
        window_end = datetime(2026, 7, 8, 14, 15, 0, tzinfo=timezone.utc)
        entry_ts = window_end - timedelta(seconds=60)
        result = _make_result(1234567890, "UP", Decimal("0.96"), Decimal("4.5"))

        snapshots: list[BookSnapshot] = []
        thresholds = StabilityThresholds()
        metrics = _compute_stability_metrics(
            result, window_end, snapshots, thresholds,
            entry_ts, "token-up-123", "token-down-456"
        )

        assert metrics.round_no == 1234567890
        assert metrics.min_leader_bid_after_entry is None
        assert metrics.max_opposite_bid_after_entry is None
        assert metrics.book_flip_warning is False
        assert metrics.first_instability_ts is None


class TestCLIParser:
    """Tests for CLI argument parser."""

    def test_parser_accepts_thresholds(self) -> None:
        """Parser should accept threshold arguments."""
        from btcbot.backtest.book_stability_diagnostics import _build_parser

        parser = _build_parser()
        args = parser.parse_args([
            "--db", "sqlite+aiosqlite:///./test.db",
            "--t-entry", "60",
            "--delta-threshold", "50",
            "--min-price", "0.96",
            "--max-price", "0.99",
            "--leader-bid-warn", "0.88",
            "--opposite-bid-warn", "0.12",
            "--leader-ask-warn", "0.91",
            "--drawdown-warn", "0.08",
        ])
        assert args.db == "sqlite+aiosqlite:///./test.db"
        assert args.t_entry == 60
        assert args.delta_threshold == 50.0
        assert args.min_price == 0.96
        assert args.max_price == 0.99
        assert args.leader_bid_warn == 0.88
        assert args.opposite_bid_warn == 0.12
        assert args.leader_ask_warn == 0.91
        assert args.drawdown_warn == 0.08


class TestStoreLifecycle:
    """Tests for Store lifecycle (regression for AttributeError bugs)."""

    async def test_main_async_uses_store_open_not_connect(self) -> None:
        """Regression: main_async must use Store.open(), not Store() + connect()."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from btcbot.backtest.book_stability_diagnostics import main_async

        # Mock Store.open to return a mock store
        mock_store = MagicMock()
        mock_store.close = AsyncMock()

        # Mock run_diagnostics to return empty diagnostics
        mock_diagnostics = MagicMock()
        mock_diagnostics.metrics = []

        with patch("btcbot.backtest.book_stability_diagnostics.Store.open", new_callable=AsyncMock) as mock_open, \
             patch("btcbot.backtest.book_stability_diagnostics.run_diagnostics", new_callable=AsyncMock) as mock_run, \
             patch("btcbot.backtest.book_stability_diagnostics.format_report") as mock_format:
            
            mock_open.return_value = mock_store
            mock_run.return_value = mock_diagnostics
            mock_format.return_value = "Test report"

            # Run main_async with minimal args
            argv = [
                "--db", "sqlite+aiosqlite:///./test.db",
                "--since", "2026-07-01T00:00:00+00:00",
            ]
            result = await main_async(argv)

            # Verify Store.open was called (not Store() constructor + connect())
            mock_open.assert_awaited_once_with("sqlite+aiosqlite:///./test.db")

            # Verify store.close was called
            mock_store.close.assert_awaited_once()

            # Verify run_diagnostics was called with the mock store
            assert mock_run.await_count == 1
            call_args = mock_run.call_args[0]
            assert call_args[0] is mock_store  # First arg should be store

            # Verify successful return
            assert result == 0


class TestReconstructTicksUsage:
    """Regression tests for reconstruct_ticks usage (TypeError bug)."""

    async def test_run_diagnostics_uses_load_round_replays_not_reconstruct_ticks(self) -> None:
        """Regression: run_diagnostics must use load_round_replays(), not call reconstruct_ticks directly.
        
        Bug was: ticks = await reconstruct_ticks(store, rnd, config.vol, config.fee_model)
        - reconstruct_ticks() is synchronous (not async)
        - reconstruct_ticks() takes 3 args (rnd, snaps, sigs), not 4
        
        Fix: use load_round_replays() async generator which handles the loading internally.
        """
        from unittest.mock import AsyncMock, MagicMock, patch
        from decimal import Decimal

        from btcbot.backtest.book_stability_diagnostics import run_diagnostics, StabilityThresholds
        from btcbot.backtest.replay import ReplayConfig
        from btcbot.domain.fees import CryptoFeesV2
        from btcbot.domain.strategy import StrategyParams
        from btcbot.exec.sizing import SizingLimits

        # Mock store
        mock_store = MagicMock()

        # Create a proper ReplayConfig (not a mock) to avoid seed TypeError
        config = ReplayConfig(
            params=StrategyParams(
                t_entry_sec=60,
                delta_threshold=Decimal("50"),
                min_price=Decimal("0.96"),
                max_price=Decimal("0.99"),
                min_edge=Decimal("0"),
                flip_ratio=Decimal("0.05"),
                hedge_fraction=Decimal("1.0"),
                p_exit=Decimal("0.40"),
            ),
            limits=SizingLimits(
                kelly_fraction=Decimal("0.25"),
                max_notional_round=Decimal("20"),
                max_bankroll_fraction=Decimal("0.1"),
                fill_safety=Decimal("0.9"),
                min_edge=Decimal("0"),
                max_price=Decimal("0.99"),
                min_order_size=Decimal("0.01"),
                tick_size=Decimal("0.01"),
            ),
            starting_balance=Decimal("500"),
            vol=Decimal("5"),
            fee_model=CryptoFeesV2(),
            latency_ticks=2,
            competition_fraction=Decimal("0.5"),
            slippage_enabled=True,
            seed=42,
        )

        # Mock thresholds
        thresholds = StabilityThresholds()

        # Mock load_round_replays to return empty (no rounds)
        async def mock_load_empty():
            return
            yield  # Make it a generator
        
        # Patch in the replay module where it's imported from
        with patch("btcbot.backtest.replay.load_round_replays", return_value=mock_load_empty()):
            # This should NOT raise TypeError about reconstruct_ticks arguments
            diagnostics = await run_diagnostics(
                mock_store,
                config,
                since=None,
                until=None,
                max_rounds=None,
                thresholds=thresholds,
            )
            
            # Should return empty diagnostics (no rounds loaded)
            assert diagnostics.metrics == []


class TestSlottedDataclass:
    """Regression tests for slotted dataclass (AttributeError __dict__ bug)."""

    def test_book_stability_metrics_is_slotted(self) -> None:
        """Verify BookStabilityMetrics is a slotted dataclass (no __dict__)."""
        from datetime import datetime, timezone
        from decimal import Decimal

        from btcbot.backtest.book_stability_diagnostics import BookStabilityMetrics

        # Create a sample metrics object
        metrics = BookStabilityMetrics(
            round_no=123,
            entry_ts=datetime(2026, 7, 8, 14, 14, 0, tzinfo=timezone.utc),
            time_left_entry=60.0,
            side_taken="UP",
            resolved_outcome="",
            result="WIN",
            pnl=Decimal("4.5"),
            entry_price=Decimal("0.96"),
            min_leader_bid_after_entry=Decimal("0.94"),
            max_opposite_bid_after_entry=Decimal("0.04"),
            min_leader_ask_after_entry=Decimal("0.96"),
            max_opposite_ask_after_entry=Decimal("0.06"),
            leader_bid_drawdown=Decimal("0.02"),
            opposite_bid_spike=Decimal("0.04"),
            leader_ask_drawdown=Decimal("0.00"),
            leader_bid_below_0_95=False,
            leader_bid_below_0_90=False,
            leader_ask_below_0_95=False,
            leader_ask_below_0_90=False,
            opposite_bid_above_0_05=False,
            opposite_bid_above_0_10=False,
            opposite_bid_above_0_15=False,
            book_flip_warning=False,
            first_instability_ts=None,
            seconds_after_entry_to_instability=None,
            time_left_at_instability=None,
        )

        # Verify it's slotted (no __dict__)
        assert not hasattr(metrics, "__dict__")

    def test_book_stability_metrics_can_use_replace_not_dict(self) -> None:
        """Regression: BookStabilityMetrics must use replace(), not __dict__.
        
        Bug was: metrics = BookStabilityMetrics(**{**metrics.__dict__, "resolved_outcome": "UP"})
        - BookStabilityMetrics is slotted, so __dict__ doesn't exist
        
        Fix: metrics = replace(metrics, resolved_outcome="UP")
        """
        from dataclasses import replace
        from datetime import datetime, timezone
        from decimal import Decimal

        from btcbot.backtest.book_stability_diagnostics import BookStabilityMetrics

        # Create a sample metrics object
        original = BookStabilityMetrics(
            round_no=123,
            entry_ts=datetime(2026, 7, 8, 14, 14, 0, tzinfo=timezone.utc),
            time_left_entry=60.0,
            side_taken="UP",
            resolved_outcome="",  # Initially empty
            result="WIN",
            pnl=Decimal("4.5"),
            entry_price=Decimal("0.96"),
            min_leader_bid_after_entry=Decimal("0.94"),
            max_opposite_bid_after_entry=Decimal("0.04"),
            min_leader_ask_after_entry=Decimal("0.96"),
            max_opposite_ask_after_entry=Decimal("0.06"),
            leader_bid_drawdown=Decimal("0.02"),
            opposite_bid_spike=Decimal("0.04"),
            leader_ask_drawdown=Decimal("0.00"),
            leader_bid_below_0_95=False,
            leader_bid_below_0_90=False,
            leader_ask_below_0_95=False,
            leader_ask_below_0_90=False,
            opposite_bid_above_0_05=False,
            opposite_bid_above_0_10=False,
            opposite_bid_above_0_15=False,
            book_flip_warning=False,
            first_instability_ts=None,
            seconds_after_entry_to_instability=None,
            time_left_at_instability=None,
        )

        # Use replace() to update resolved_outcome (works with slotted dataclass)
        updated = replace(original, resolved_outcome="UP")

        # Verify update worked
        assert original.resolved_outcome == ""
        assert updated.resolved_outcome == "UP"
        assert updated.round_no == 123
        assert updated.side_taken == "UP"
