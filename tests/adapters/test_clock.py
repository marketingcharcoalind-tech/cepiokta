"""Unit tests for btcbot.adapters.clock."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from btcbot.adapters.clock import Clock, SimClock, SystemClock


class TestSystemClock:
    def test_now_is_utc_aware(self) -> None:
        clock = SystemClock()
        t = clock.now()
        assert t.tzinfo is not None
        assert t.utcoffset() == timedelta(0)

    def test_satisfies_clock_protocol(self) -> None:
        assert isinstance(SystemClock(), Clock)

    def test_now_advances_monotonically(self) -> None:
        clock = SystemClock()
        t1 = clock.now()
        t2 = clock.now()
        assert t2 >= t1


class TestSimClock:
    def test_default_start_is_epoch_utc(self) -> None:
        clock = SimClock()
        assert clock.now() == datetime(1970, 1, 1, tzinfo=UTC)

    def test_now_is_utc_aware(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        t = clock.now()
        assert t.tzinfo is not None
        assert t.utcoffset() == timedelta(0)

    def test_satisfies_clock_protocol(self) -> None:
        assert isinstance(SimClock(), Clock)

    def test_now_is_deterministic(self) -> None:
        start = datetime(2026, 6, 25, 10, 30, tzinfo=UTC)
        clock = SimClock(start)
        # Tidak berjalan otomatis: pemanggilan berulang sama persis.
        assert clock.now() == start
        assert clock.now() == start

    def test_set_changes_time(self) -> None:
        clock = SimClock()
        target = datetime(2026, 3, 1, 8, 15, 30, tzinfo=UTC)
        clock.set(target)
        assert clock.now() == target

    def test_advance_returns_new_time(self) -> None:
        start = datetime(2026, 6, 25, 0, 0, tzinfo=UTC)
        clock = SimClock(start)
        result = clock.advance(timedelta(minutes=5))
        assert result == datetime(2026, 6, 25, 0, 5, tzinfo=UTC)
        assert clock.now() == result

    def test_advance_accumulates(self) -> None:
        start = datetime(2026, 6, 25, 0, 0, tzinfo=UTC)
        clock = SimClock(start)
        clock.advance(timedelta(seconds=30))
        clock.advance(timedelta(seconds=30))
        assert clock.now() == start + timedelta(minutes=1)

    def test_advance_negative_goes_back(self) -> None:
        start = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
        clock = SimClock(start)
        clock.advance(timedelta(seconds=-10))
        assert clock.now() == datetime(2026, 6, 25, 11, 59, 50, tzinfo=UTC)

    def test_non_utc_input_normalized_to_utc(self) -> None:
        # Input tz +02:00 dinormalisasi ke UTC.
        tz = timezone(timedelta(hours=2))
        clock = SimClock(datetime(2026, 1, 1, 14, 0, tzinfo=tz))
        assert clock.now() == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert clock.now().utcoffset() == timedelta(0)

    def test_naive_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="tz-aware"):
            SimClock(datetime(2026, 1, 1, 0, 0))  # noqa: DTZ001

    def test_naive_set_rejected(self) -> None:
        clock = SimClock()
        with pytest.raises(ValueError, match="tz-aware"):
            clock.set(datetime(2026, 1, 1, 0, 0))  # noqa: DTZ001
