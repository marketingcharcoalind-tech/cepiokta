"""domain/market.py — interval-loader (docs/08 §8.6, docs/05).

Logika **window/interval** ronde Up/Down sebagai domain **murni** (tanpa I/O).
Sumber waktu di-inject (``now`` eksplisit atau :class:`~btcbot.adapters.clock.Clock`)
agar deterministik & mudah dites.

Fakta cadence terverifikasi (lihat PROMPT_GUIDE ✅ VERIFIED REALITY #1):

- Slug market: ``{asset}-updown-{5m|15m}-{epoch}`` (mis. ``btc-updown-5m-1782480000``).
- ``epoch`` = waktu resolusi = ``window_end``; ``round_no = epoch``.
- Cadence: ``epoch % 300 == 0`` (5m) / ``epoch % 900 == 0`` (15m).
- Cross-check: ``1782480000`` → ``2026-06-26T13:20:00Z`` (``window_end``;
  ``window_start`` = ``13:15:00Z``).

Konvensi window: **half-open** ``[window_start, window_end)`` — konsisten dengan
:meth:`HttpGammaClient.discover_active_round` (``start <= now < end``). Pada
instant tepat ``window_end``, window tersebut sudah berakhir (eksklusif) dan
window berikutnya dimulai.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from btcbot.adapters.clock import Clock
    from btcbot.domain.models import Round

# Detik per timeframe (sumber tunggal; selaras adapters/gamma.py).
TIMEFRAME_SECONDS: dict[str, int] = {"5m": 300, "15m": 900}


def timeframe_seconds(timeframe: str) -> int:
    """Kembalikan jumlah detik untuk ``timeframe`` (``5m``/``15m``).

    Raises:
        ValueError: bila ``timeframe`` tak didukung.
    """
    try:
        return TIMEFRAME_SECONDS[timeframe]
    except KeyError as exc:
        raise ValueError(f"timeframe tak didukung: {timeframe!r} (pilih 5m/15m)") from exc


def _ensure_utc(now: datetime) -> datetime:
    """Validasi ``now`` tz-aware lalu normalisasi ke UTC."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now membutuhkan datetime tz-aware (bukan naive)")
    return now.astimezone(UTC)


def aligned_window(now: datetime, timeframe: str) -> tuple[datetime, datetime]:
    """Hitung batas window ber-cadence yang memuat ``now`` (pure math).

    Mengembalikan ``(window_start, window_end)`` half-open ``[start, end)`` yang
    sejajar kelipatan epoch timeframe. Tidak butuh data Gamma — berguna untuk
    penjadwalan scanner (Fase 1+) bahkan tanpa ronde ter-discover.

    Args:
        now: Waktu acuan (tz-aware UTC).
        timeframe: ``5m`` | ``15m``.
    """
    period = timeframe_seconds(timeframe)
    now_utc = _ensure_utc(now)
    epoch = now_utc.timestamp()
    start_epoch = (int(epoch) // period) * period
    start = datetime.fromtimestamp(start_epoch, tz=UTC)
    end = start + timedelta(seconds=period)
    return start, end


def round_no_for(now: datetime, timeframe: str) -> int:
    """Kembalikan ``round_no`` (= epoch ``window_end``) window yang memuat ``now``.

    ``round_no`` selalu kelipatan ``timeframe_seconds`` (cadence terverifikasi).
    """
    _, end = aligned_window(now, timeframe)
    return int(end.timestamp())


class IntervalLoader:
    """Pemilih window ronde aktif dari kumpulan :class:`Round` ter-discover.

    Domain murni: tidak melakukan I/O. ``Round`` di-inject (hasil discovery
    Gamma di lapisan app). Waktu boleh di-pass eksplisit (``now``) atau diambil
    dari :class:`Clock` yang di-inject (deterministik via ``SimClock``).

    Args:
        rounds: Kumpulan ronde (boleh kosong / tak terurut).
        clock: Sumber waktu opsional; dipakai bila ``now`` tidak diberikan.

    Raises:
        ValueError: bila sebuah ronde memiliki window tidak valid
            (``window_start``/``window_end`` naive atau ``end <= start``).
    """

    def __init__(self, rounds: Iterable[Round], *, clock: Clock | None = None) -> None:
        validated: list[Round] = []
        for rnd in rounds:
            start = _ensure_utc(rnd.window_start)
            end = _ensure_utc(rnd.window_end)
            if end <= start:
                raise ValueError(
                    f"ronde {rnd.round_no}: window_end ({end}) harus > window_start ({start})"
                )
            validated.append(rnd)
        # Urut berdasarkan window_start agar pemilihan deterministik.
        self._rounds: tuple[Round, ...] = tuple(sorted(validated, key=lambda r: r.window_start))
        self._clock = clock

    def _resolve_now(self, now: datetime | None) -> datetime:
        """Tentukan waktu acuan: ``now`` eksplisit, atau dari clock yang di-inject."""
        if now is not None:
            return _ensure_utc(now)
        if self._clock is None:
            raise ValueError("now tidak diberikan dan tidak ada Clock yang di-inject")
        return _ensure_utc(self._clock.now())

    def current_window(self, now: datetime | None = None) -> Round | None:
        """Kembalikan ronde yang memuat ``now`` (``start <= now < end``).

        Args:
            now: Waktu acuan (tz-aware). Bila None, pakai clock yang di-inject.

        Returns:
            :class:`Round` aktif, atau ``None`` bila ``now`` di luar semua window
            (sebelum ronde pertama, di antara ronde, atau setelah ronde terakhir).
        """
        ref = self._resolve_now(now)
        for rnd in self._rounds:
            if rnd.window_start <= ref < rnd.window_end:
                return rnd
        return None

    def time_left(self, now: datetime | None = None) -> float:
        """Detik tersisa pada window aktif (``window_end - now``).

        Returns:
            Sisa detik (> 0) bila ``now`` berada dalam suatu window; ``0.0`` bila
            tidak ada window aktif.
        """
        ref = self._resolve_now(now)
        rnd = self.current_window(ref)
        if rnd is None:
            return 0.0
        return (rnd.window_end - ref).total_seconds()

    def is_entry_window(self, now: datetime | None = None, t_entry_sec: float = 0.0) -> bool:
        """True bila sekarang di dalam window & sisa waktu ``<= t_entry_sec``.

        Mengikuti aturan entry docs/05 §5.4: entry hanya bila
        ``0 < time_left <= T_ENTRY_SEC``. Di luar window (``time_left == 0``)
        selalu ``False``.

        Args:
            now: Waktu acuan (tz-aware). Bila None, pakai clock yang di-inject.
            t_entry_sec: Ambang sisa-waktu (detik) untuk mulai entry.
        """
        ref = self._resolve_now(now)
        remaining = self.time_left(ref)
        return 0.0 < remaining <= t_entry_sec
