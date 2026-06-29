"""app/price_sampler.py — sampler harga BTC/USD periodik (Bug B9).

Merekam **trajektori** harga Chainlink BTC/USD ke tabel ``signals`` SELAMA ronde,
berjalan PARALEL dengan ``recorder.consume_market`` (book streaming). Sebelumnya
harga hanya direkam SEKALI/ronde → ``delta`` beku di ``start_price`` → ``p_win``=0.5
→ 0 entry di backtest. Sampler ini memberi ``price_now`` yang bergerak per tick.

Freeze-safe (pola B-freeze): deadline ``window_end + drain``, sleep ter-timeout &
shutdown-aware, tahan ``PriceUnavailableError`` (skip + lanjut, JANGAN crash /
JANGAN hentikan book recording). READ-ONLY (tanpa order).

Anti-spam RPC: tulis hanya saat harga **berubah**, ATAU sudah lewat cadence
(``force_seconds`` normal / ``tail_seconds`` di ekor). Di ekor-window
(``tail_window`` detik terakhir) cadence dipercepat karena di situ keputusan
terjadi & presisi paling penting.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from btcbot.domain.models import Signal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from btcbot.adapters.clock import Clock
    from btcbot.data.store import Store
    from btcbot.domain.models import PriceSource, Round

    SleepFunc = Callable[[float], Awaitable[None]]

_log = structlog.get_logger("btcbot.price_sampler")
_ZERO = Decimal("0")


def _leader(delta: Decimal) -> str:
    if delta > 0:
        return "UP"
    if delta < 0:
        return "DOWN"
    return ""


class PriceSampler:
    """Sampler harga periodik untuk satu ronde (tulis ``signals`` price_now+ts).

    Args:
        store: Persistensi tujuan (tabel ``signals``).
        price_source: Sumber harga Chainlink (FailoverPriceSource / Fake).
        clock: Sumber waktu (deadline & time_left).
        mode: Label mode untuk baris signal (default ``readonly``).
        sample_seconds: Cadence poll/tulis normal.
        tail_seconds: Cadence di ekor-window (lebih rapat).
        tail_window_seconds: Lebar ekor-window (detik sebelum ``window_end``).
        drain_seconds: Lanjut sampling sampai ``window_end + ini`` (tangkap settle).
        force_seconds: Tulis min. 1x per interval ini meski harga sama (heartbeat).
        sleep: Fungsi tidur (injectable untuk test).
    """

    def __init__(  # noqa: PLR0913 - parameter konfigurasi eksplisit
        self,
        store: Store,
        price_source: PriceSource,
        clock: Clock,
        *,
        mode: str = "readonly",
        sample_seconds: float = 2.0,
        tail_seconds: float = 0.5,
        tail_window_seconds: int = 60,
        drain_seconds: int = 3,
        force_seconds: float = 25.0,
        sleep: SleepFunc = asyncio.sleep,
    ) -> None:
        self._store = store
        self._price_source = price_source
        self._clock = clock
        self._mode = mode
        self._sample_seconds = sample_seconds
        self._tail_seconds = tail_seconds
        self._tail_window_seconds = tail_window_seconds
        self._drain_seconds = drain_seconds
        self._force_seconds = force_seconds
        self._sleep = sleep

    async def run(self, rnd: Round, *, shutdown: asyncio.Event | None = None) -> int:
        """Sampel harga sampai ``window_end + drain``. Kembalikan jumlah baris ditulis.

        Tahan ``PriceUnavailableError`` (skip + lanjut). Berhenti bersih saat
        deadline / shutdown (tidak menggantung).
        """
        deadline = rnd.window_end + timedelta(seconds=self._drain_seconds)
        written = 0
        last_price: Decimal | None = None
        last_write = self._clock.now()

        while True:
            now = self._clock.now()
            if now >= deadline:
                break
            if shutdown is not None and shutdown.is_set():
                break

            remaining = (rnd.window_end - now).total_seconds()
            in_tail = remaining <= self._tail_window_seconds
            cadence = self._tail_seconds if in_tail else self._force_seconds
            interval = self._tail_seconds if in_tail else self._sample_seconds

            try:
                tick = await self._price_source.price_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning("price_sample_skip", round_no=rnd.round_no, error=str(exc))
                await self._sleep(interval)
                continue

            elapsed = (now - last_write).total_seconds()
            changed = last_price is None or tick.price != last_price
            if changed or elapsed >= cadence:
                delta = tick.price - rnd.start_price
                await self._store.insert_signal(
                    Signal(
                        round_no=rnd.round_no,
                        ts=tick.ts,
                        price_now=tick.price,
                        delta=delta,
                        time_left_sec=remaining,
                        p_win=_ZERO,
                        leader=_leader(delta),
                        ask_win=_ZERO,
                        net_edge=_ZERO,
                    ),
                    mode=self._mode,
                )
                written += 1
                last_price = tick.price
                last_write = now

            await self._sleep(interval)

        return written
