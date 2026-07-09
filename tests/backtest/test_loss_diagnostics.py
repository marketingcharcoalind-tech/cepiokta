"""test_loss_diagnostics.py — Unit tests untuk loss diagnostics bucket functions."""

from decimal import Decimal

import pytest

from btcbot.backtest.loss_diagnostics import (
    BucketStats,
    EntryDetail,
    LossDiagnostics,
    _bucket_abs_delta,
    _bucket_entry_price,
    _bucket_p_win,
    _bucket_time_left,
)


class TestBucketFunctions:
    """Test bucket categorization functions."""

    def test_bucket_entry_price(self) -> None:
        """Test entry price bucketing."""
        assert _bucket_entry_price(Decimal("0.90")) == "<=0.95"
        assert _bucket_entry_price(Decimal("0.95")) == "<=0.95"
        assert _bucket_entry_price(Decimal("0.96")) == "(0.95,0.97]"
        assert _bucket_entry_price(Decimal("0.97")) == "(0.95,0.97]"
        assert _bucket_entry_price(Decimal("0.98")) == "(0.97,0.99]"
        assert _bucket_entry_price(Decimal("0.99")) == "(0.97,0.99]"
        assert _bucket_entry_price(Decimal("0.995")) == ">0.99"
        assert _bucket_entry_price(Decimal("1.00")) == ">0.99"

    def test_bucket_abs_delta(self) -> None:
        """Test abs_delta bucketing."""
        assert _bucket_abs_delta(Decimal("45")) == "[0,50)"
        assert _bucket_abs_delta(Decimal("50")) == "[50,60)"
        assert _bucket_abs_delta(Decimal("55")) == "[50,60)"
        assert _bucket_abs_delta(Decimal("60")) == "[60,75)"
        assert _bucket_abs_delta(Decimal("70")) == "[60,75)"
        assert _bucket_abs_delta(Decimal("75")) == "[75,100)"
        assert _bucket_abs_delta(Decimal("90")) == "[75,100)"
        assert _bucket_abs_delta(Decimal("100")) == "[100+)"
        assert _bucket_abs_delta(Decimal("150")) == "[100+)"

    def test_bucket_time_left(self) -> None:
        """Test time_left bucketing."""
        assert _bucket_time_left(10.0) == "[0,15)"
        assert _bucket_time_left(15.0) == "[15,30)"
        assert _bucket_time_left(20.0) == "[15,30)"
        assert _bucket_time_left(30.0) == "[30,45)"
        assert _bucket_time_left(40.0) == "[30,45)"
        assert _bucket_time_left(45.0) == "[45,60]"
        assert _bucket_time_left(50.0) == "[45,60]"
        assert _bucket_time_left(60.0) == "[45,60]"
        assert _bucket_time_left(65.0) == "(60+)"

    def test_bucket_p_win(self) -> None:
        """Test p_win bucketing."""
        assert _bucket_p_win(Decimal("0.75")) == "[0.50,0.80)"
        assert _bucket_p_win(Decimal("0.80")) == "[0.80,0.90)"
        assert _bucket_p_win(Decimal("0.85")) == "[0.80,0.90)"
        assert _bucket_p_win(Decimal("0.90")) == "[0.90,0.95)"
        assert _bucket_p_win(Decimal("0.92")) == "[0.90,0.95)"
        assert _bucket_p_win(Decimal("0.95")) == "[0.95,0.98)"
        assert _bucket_p_win(Decimal("0.97")) == "[0.95,0.98)"
        assert _bucket_p_win(Decimal("0.98")) == "[0.98,1.00]"
        assert _bucket_p_win(Decimal("0.99")) == "[0.98,1.00]"


class TestBucketStats:
    """Test BucketStats aggregation."""

    def test_empty_bucket(self) -> None:
        """Test empty bucket stats."""
        stats = BucketStats()
        assert stats.entries == 0
        assert stats.wins == 0
        assert stats.losses == 0
        assert stats.win_rate == 0.0
        assert stats.avg_pnl == Decimal("0")

    def test_single_win(self) -> None:
        """Test bucket with single win."""
        stats = BucketStats(entries=1, wins=1, losses=0, pnl_sum=Decimal("5.50"))
        assert stats.win_rate == 1.0
        assert stats.avg_pnl == Decimal("5.50")

    def test_mixed_results(self) -> None:
        """Test bucket with mixed win/loss."""
        stats = BucketStats(entries=10, wins=7, losses=3, pnl_sum=Decimal("12.30"))
        assert stats.win_rate == 0.7
        assert stats.avg_pnl == Decimal("1.23")


class TestLossDiagnostics:
    """Test LossDiagnostics collector."""

    @pytest.fixture
    def sample_entry_win(self) -> EntryDetail:
        """Create a sample winning entry."""
        from datetime import UTC, datetime

        return EntryDetail(
            round_no=1,
            window_start=datetime(2026, 7, 6, 1, 0, tzinfo=UTC),
            window_end=datetime(2026, 7, 6, 1, 5, tzinfo=UTC),
            start_price=Decimal("104500"),
            resolved_outcome="UP",
            entry_ts=datetime(2026, 7, 6, 1, 4, tzinfo=UTC),
            time_left_sec=45.0,
            side_taken="UP",
            leader="UP",
            entry_price=Decimal("0.98"),
            size=Decimal("10"),
            price_now=Decimal("104550"),
            delta=Decimal("60"),
            abs_delta=Decimal("60"),
            p_win=Decimal("0.96"),
            ask_win=Decimal("0.98"),
            net_edge=Decimal("0.01"),
            max_price_config=Decimal("0.99"),
            result="WIN",
            pnl=Decimal("1.50"),
        )

    @pytest.fixture
    def sample_entry_loss(self) -> EntryDetail:
        """Create a sample losing entry."""
        from datetime import UTC, datetime

        return EntryDetail(
            round_no=2,
            window_start=datetime(2026, 7, 6, 1, 5, tzinfo=UTC),
            window_end=datetime(2026, 7, 6, 1, 10, tzinfo=UTC),
            start_price=Decimal("104500"),
            resolved_outcome="DOWN",
            entry_ts=datetime(2026, 7, 6, 1, 9, tzinfo=UTC),
            time_left_sec=30.0,
            side_taken="UP",
            leader="UP",
            entry_price=Decimal("0.99"),
            size=Decimal("10"),
            price_now=Decimal("104530"),
            delta=Decimal("55"),
            abs_delta=Decimal("55"),
            p_win=Decimal("0.97"),
            ask_win=Decimal("0.99"),
            net_edge=Decimal("0.005"),
            max_price_config=Decimal("0.99"),
            result="LOSS",
            pnl=Decimal("-9.50"),
        )

    def test_empty_diagnostics(self) -> None:
        """Test empty diagnostics."""
        diag = LossDiagnostics()
        summary = diag.summary()
        assert summary["total_entries"] == 0
        assert summary["wins"] == 0
        assert summary["losses"] == 0
        assert summary["win_rate"] == 0.0
        assert summary["net_pnl"] == Decimal("0")

    def test_add_single_entry(self, sample_entry_win: EntryDetail) -> None:
        """Test adding single entry."""
        diag = LossDiagnostics()
        diag.add(sample_entry_win)
        
        summary = diag.summary()
        assert summary["total_entries"] == 1
        assert summary["wins"] == 1
        assert summary["losses"] == 0
        assert summary["win_rate"] == 1.0
        assert summary["net_pnl"] == Decimal("1.50")

    def test_add_mixed_entries(
        self, sample_entry_win: EntryDetail, sample_entry_loss: EntryDetail
    ) -> None:
        """Test adding multiple entries with mixed results."""
        diag = LossDiagnostics()
        diag.add(sample_entry_win)
        diag.add(sample_entry_loss)
        
        summary = diag.summary()
        assert summary["total_entries"] == 2
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["win_rate"] == 0.5
        assert summary["net_pnl"] == Decimal("-8.00")  # 1.50 - 9.50

    def test_loss_by_side(
        self, sample_entry_win: EntryDetail, sample_entry_loss: EntryDetail
    ) -> None:
        """Test loss_by_side bucketing."""
        diag = LossDiagnostics()
        diag.add(sample_entry_win)
        diag.add(sample_entry_loss)
        
        by_side = diag.loss_by_side()
        assert "UP" in by_side
        stats = by_side["UP"]
        assert stats.entries == 2
        assert stats.wins == 1
        assert stats.losses == 1

    def test_bucket_by_entry_price(
        self, sample_entry_win: EntryDetail, sample_entry_loss: EntryDetail
    ) -> None:
        """Test bucket_by_entry_price."""
        diag = LossDiagnostics()
        diag.add(sample_entry_win)  # 0.98 -> (0.97,0.99]
        diag.add(sample_entry_loss)  # 0.99 -> (0.97,0.99]
        
        buckets = diag.bucket_by_entry_price()
        assert "(0.97,0.99]" in buckets
        stats = buckets["(0.97,0.99]"]
        assert stats.entries == 2
        assert stats.wins == 1
        assert stats.losses == 1

    def test_bucket_by_abs_delta(
        self, sample_entry_win: EntryDetail, sample_entry_loss: EntryDetail
    ) -> None:
        """Test bucket_by_abs_delta."""
        diag = LossDiagnostics()
        diag.add(sample_entry_win)  # 60 -> [60,75)
        diag.add(sample_entry_loss)  # 55 -> [50,60)
        
        buckets = diag.bucket_by_abs_delta()
        assert "[60,75)" in buckets
        assert "[50,60)" in buckets
        assert buckets["[60,75)"].entries == 1
        assert buckets["[50,60)"].entries == 1

    def test_bucket_by_time_left(
        self, sample_entry_win: EntryDetail, sample_entry_loss: EntryDetail
    ) -> None:
        """Test bucket_by_time_left."""
        diag = LossDiagnostics()
        diag.add(sample_entry_win)  # 45s -> [45,60]
        diag.add(sample_entry_loss)  # 30s -> [30,45)
        
        buckets = diag.bucket_by_time_left()
        assert "[45,60]" in buckets
        assert "[30,45)" in buckets

    def test_bucket_by_p_win(
        self, sample_entry_win: EntryDetail, sample_entry_loss: EntryDetail
    ) -> None:
        """Test bucket_by_p_win."""
        diag = LossDiagnostics()
        diag.add(sample_entry_win)  # 0.96 -> [0.95,0.98)
        diag.add(sample_entry_loss)  # 0.97 -> [0.95,0.98)
        
        buckets = diag.bucket_by_p_win()
        assert "[0.95,0.98)" in buckets
        stats = buckets["[0.95,0.98)"]
        assert stats.entries == 2
        assert stats.wins == 1
        assert stats.losses == 1
