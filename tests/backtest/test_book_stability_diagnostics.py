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
