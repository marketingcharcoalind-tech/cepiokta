"""Clock adapter — injectable time source (docs/08 §8.1).

Menyediakan abstraksi waktu agar domain & test deterministik:
- :class:`Clock` — Protocol (kontrak ``now() -> datetime`` UTC aware).
- :class:`SystemClock` — implementasi produksi (waktu nyata UTC).
- :class:`SimClock` — implementasi simulasi untuk backtest/test
  (waktu di-set/advance secara manual).

Aturan (lihat AGENTS.md & docs/03 §3.5): semua datetime WAJIB tz-aware UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Kontrak sumber waktu. ``now()`` mengembalikan datetime UTC aware."""

    def now(self) -> datetime:
        """Kembalikan waktu saat ini sebagai datetime tz-aware UTC."""
        ...


class SystemClock:
    """Clock produksi: membaca waktu sistem nyata dalam UTC."""

    def now(self) -> datetime:
        """Kembalikan ``datetime.now`` dalam UTC (tz-aware)."""
        return datetime.now(UTC)


class SimClock:
    """Clock simulasi deterministik untuk backtest & unit test.

    Waktu tidak berjalan otomatis; dikendalikan via :meth:`set` dan
    :meth:`advance`. Semua waktu disimpan & dikembalikan sebagai UTC aware.

    Args:
        start: Waktu awal. Wajib tz-aware. Jika None, default epoch UTC.

    Raises:
        ValueError: bila ``start`` tidak tz-aware.
    """

    def __init__(self, start: datetime | None = None) -> None:
        if start is None:
            start = datetime(1970, 1, 1, tzinfo=UTC)
        self._current = self._ensure_utc(start)

    @staticmethod
    def _ensure_utc(t: datetime) -> datetime:
        """Pastikan datetime tz-aware lalu normalisasi ke UTC."""
        if t.tzinfo is None or t.utcoffset() is None:
            raise ValueError("SimClock membutuhkan datetime tz-aware (bukan naive)")
        return t.astimezone(UTC)

    def now(self) -> datetime:
        """Kembalikan waktu simulasi saat ini (UTC aware)."""
        return self._current

    def set(self, t: datetime) -> None:
        """Set waktu simulasi ke ``t`` (wajib tz-aware).

        Raises:
            ValueError: bila ``t`` naive (tanpa tzinfo).
        """
        self._current = self._ensure_utc(t)

    def advance(self, delta: timedelta) -> datetime:
        """Majukan waktu simulasi sebesar ``delta`` dan kembalikan waktu baru.

        Args:
            delta: Durasi maju (boleh negatif untuk mundur).

        Returns:
            Waktu simulasi setelah dimajukan.
        """
        self._current = self._current + delta
        return self._current
