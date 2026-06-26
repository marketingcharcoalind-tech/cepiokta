"""Unit tests for btcbot.domain.market (interval-loader).

Domain murni & deterministik: waktu di-pass eksplisit atau via SimClock.
Edge cases: sebelum/saat/sesudah window, batas T_ENTRY_SEC, cadence epoch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from btcbot.adapters.clock import SimClock
from btcbot.domain.market import (
    IntervalLoader,
    aligned_window,
    round_no_for,
    timeframe_seconds,
)
from btcbot.domain.models import Round, RoundStatus

# Cross-check terverifikasi: epoch 1782480000 == 2026-06-26T13:20:00Z (window_end).
FIXTURE_EPOCH = 1782480000
FIXTURE_END = datetime(2026, 6, 26, 13, 20, 0, tzinfo=UTC)
FIXTURE_START = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)


def make_round(
    round_no: int,
    start: datetime,
    end: datetime,
    *,
    status: RoundStatus = RoundStatus.ACTIVE,
) -> Round:
    """Bangun :class:`Round` minimal untuk test interval."""
    return Round(
        condition_id=f"0xcond{round_no}",
        round_no=round_no,
        token_id_up=f"up{round_no}",
        token_id_down=f"down{round_no}",
        window_start=start,
        window_end=end,
        start_price=Decimal("65000"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        status=status,
    )


class TestTimeframeSeconds:
    def test_5m(self) -> None:
        assert timeframe_seconds("5m") == 300

    def test_15m(self) -> None:
        assert timeframe_seconds("15m") == 900

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="tak didukung"):
            timeframe_seconds("1m")


class TestAlignedWindow:
    def test_fixture_cross_check(self) -> None:
        # now di dalam window fixture → batas sejajar cadence.
        now = FIXTURE_START + timedelta(seconds=42)
        start, end = aligned_window(now, "5m")
        assert start == FIXTURE_START
        assert end == FIXTURE_END

    def test_start_inclusive(self) -> None:
        start, end = aligned_window(FIXTURE_START, "5m")
        assert start == FIXTURE_START
        assert end == FIXTURE_END

    def test_end_rolls_to_next(self) -> None:
        # Tepat di window_end → sudah masuk window berikutnya.
        start, end = aligned_window(FIXTURE_END, "5m")
        assert start == FIXTURE_END
        assert end == FIXTURE_END + timedelta(minutes=5)

    def test_15m_alignment(self) -> None:
        now = datetime(2026, 6, 26, 13, 7, 0, tzinfo=UTC)
        start, end = aligned_window(now, "15m")
        assert start == datetime(2026, 6, 26, 13, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)

    def test_non_utc_input_normalized(self) -> None:
        tz = timezone(timedelta(hours=2))
        now = datetime(2026, 6, 26, 15, 17, 0, tzinfo=tz)  # == 13:17:00Z
        start, end = aligned_window(now, "5m")
        assert start == FIXTURE_START
        assert end == FIXTURE_END

    def test_naive_rejected(self) -> None:
        with pytest.raises(ValueError, match="tz-aware"):
            aligned_window(datetime(2026, 6, 26, 13, 17, 0), "5m")  # noqa: DTZ001


class TestRoundNoFor:
    def test_equals_window_end_epoch(self) -> None:
        now = FIXTURE_START + timedelta(seconds=1)
        assert round_no_for(now, "5m") == FIXTURE_EPOCH

    def test_is_multiple_of_period(self) -> None:
        now = datetime(2026, 6, 26, 13, 7, 33, tzinfo=UTC)
        rn5 = round_no_for(now, "5m")
        rn15 = round_no_for(now, "15m")
        assert rn5 % 300 == 0
        assert rn15 % 900 == 0


class TestIntervalLoaderCurrentWindow:
    def _loader(self) -> IntervalLoader:
        r1 = make_round(FIXTURE_EPOCH, FIXTURE_START, FIXTURE_END)
        # Ronde berikutnya dengan GAP 5 menit (bukan kontigu) untuk uji celah.
        gap_start = FIXTURE_END + timedelta(minutes=5)
        next_no = int(gap_start.timestamp()) + 300
        r2 = make_round(next_no, gap_start, gap_start + timedelta(minutes=5))
        return IntervalLoader([r2, r1])  # sengaja tak terurut

    def test_inside_window(self) -> None:
        loader = self._loader()
        rnd = loader.current_window(FIXTURE_START + timedelta(seconds=120))
        assert rnd is not None
        assert rnd.round_no == FIXTURE_EPOCH

    def test_start_inclusive(self) -> None:
        loader = self._loader()
        rnd = loader.current_window(FIXTURE_START)
        assert rnd is not None
        assert rnd.round_no == FIXTURE_EPOCH

    def test_end_exclusive(self) -> None:
        loader = self._loader()
        # Tepat window_end → window pertama sudah berakhir; ada gap → None.
        assert loader.current_window(FIXTURE_END) is None

    def test_before_all_windows(self) -> None:
        loader = self._loader()
        assert loader.current_window(FIXTURE_START - timedelta(seconds=1)) is None

    def test_in_gap_between_windows(self) -> None:
        loader = self._loader()
        assert loader.current_window(FIXTURE_END + timedelta(minutes=1)) is None

    def test_after_all_windows(self) -> None:
        loader = self._loader()
        assert loader.current_window(FIXTURE_END + timedelta(hours=2)) is None

    def test_empty_loader(self) -> None:
        loader = IntervalLoader([])
        assert loader.current_window(FIXTURE_START) is None


class TestIntervalLoaderTimeLeft:
    def test_remaining_seconds(self) -> None:
        loader = IntervalLoader([make_round(FIXTURE_EPOCH, FIXTURE_START, FIXTURE_END)])
        now = FIXTURE_END - timedelta(seconds=18)
        assert loader.time_left(now) == pytest.approx(18.0)

    def test_full_window_at_start(self) -> None:
        loader = IntervalLoader([make_round(FIXTURE_EPOCH, FIXTURE_START, FIXTURE_END)])
        assert loader.time_left(FIXTURE_START) == pytest.approx(300.0)

    def test_zero_when_no_active_window(self) -> None:
        loader = IntervalLoader([make_round(FIXTURE_EPOCH, FIXTURE_START, FIXTURE_END)])
        assert loader.time_left(FIXTURE_END) == 0.0
        assert loader.time_left(FIXTURE_START - timedelta(seconds=5)) == 0.0


class TestIntervalLoaderIsEntryWindow:
    def _loader(self) -> IntervalLoader:
        return IntervalLoader([make_round(FIXTURE_EPOCH, FIXTURE_START, FIXTURE_END)])

    def test_too_early_in_window(self) -> None:
        # 120s tersisa > T_ENTRY 20 → belum entry.
        loader = self._loader()
        now = FIXTURE_END - timedelta(seconds=120)
        assert loader.is_entry_window(now, 20) is False

    def test_within_entry_threshold(self) -> None:
        loader = self._loader()
        now = FIXTURE_END - timedelta(seconds=15)
        assert loader.is_entry_window(now, 20) is True

    def test_boundary_exactly_t_entry_is_inclusive(self) -> None:
        loader = self._loader()
        now = FIXTURE_END - timedelta(seconds=20)
        assert loader.is_entry_window(now, 20) is True

    def test_at_window_end_is_false(self) -> None:
        # time_left == 0 → bukan entry (window sudah tutup).
        loader = self._loader()
        assert loader.is_entry_window(FIXTURE_END, 20) is False

    def test_outside_window_is_false(self) -> None:
        loader = self._loader()
        assert loader.is_entry_window(FIXTURE_START - timedelta(seconds=1), 20) is False


class TestIntervalLoaderClockInjection:
    def test_uses_injected_clock_when_now_omitted(self) -> None:
        clock = SimClock(FIXTURE_START + timedelta(seconds=10))
        loader = IntervalLoader(
            [make_round(FIXTURE_EPOCH, FIXTURE_START, FIXTURE_END)], clock=clock
        )
        rnd = loader.current_window()
        assert rnd is not None
        assert rnd.round_no == FIXTURE_EPOCH
        assert loader.time_left() == pytest.approx(290.0)

    def test_clock_advance_changes_result(self) -> None:
        clock = SimClock(FIXTURE_START)
        loader = IntervalLoader(
            [make_round(FIXTURE_EPOCH, FIXTURE_START, FIXTURE_END)], clock=clock
        )
        assert loader.is_entry_window(t_entry_sec=20) is False
        clock.set(FIXTURE_END - timedelta(seconds=10))
        assert loader.is_entry_window(t_entry_sec=20) is True

    def test_no_now_and_no_clock_raises(self) -> None:
        loader = IntervalLoader([make_round(FIXTURE_EPOCH, FIXTURE_START, FIXTURE_END)])
        with pytest.raises(ValueError, match="tidak ada Clock"):
            loader.current_window()


class TestIntervalLoaderValidation:
    def test_naive_window_rejected(self) -> None:
        bad = make_round(
            FIXTURE_EPOCH,
            datetime(2026, 6, 26, 13, 20, 0),  # noqa: DTZ001 - sengaja naive
            FIXTURE_END,
        )
        with pytest.raises(ValueError, match="tz-aware"):
            IntervalLoader([bad])

    def test_end_not_after_start_rejected(self) -> None:
        bad = make_round(FIXTURE_EPOCH, FIXTURE_END, FIXTURE_START)
        with pytest.raises(ValueError, match="window_end"):
            IntervalLoader([bad])
