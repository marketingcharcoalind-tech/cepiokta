"""Fase-0 recorder (docs/08 §8.12, docs/10 Fase 0).

Mengkonsumsi stream Gamma (metadata ronde), CLOB WSS (orderbook), dan Chainlink
(harga) lalu menulis ``rounds``, ``book_snapshots``, ``signals``, dan resolusi
ke :class:`~btcbot.data.store.Store`. **TANPA order** (mode readonly).

Saat WSS putus/stale, event dari adapter WSS (:class:`CircuitEvent`) ditangkap
via :meth:`Recorder.on_circuit_event` dan ditulis sebagai penanda *gap* di
``book_snapshots`` (Requirement 1: "menandai data sebagai gap").
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from btcbot.adapters.clob_ws import CircuitEvent, EventType
from btcbot.domain.models import RoundStatus, Signal

if TYPE_CHECKING:
    from btcbot.adapters.clob_ws import ClobWS
    from btcbot.adapters.clock import Clock
    from btcbot.data.store import Store
    from btcbot.domain.models import Outcome, PriceSource, PriceTick, Round

# Event yang menandakan data tidak kontinu → tandai gap.
_GAP_EVENTS = frozenset({EventType.DISCONNECTED, EventType.STALE, EventType.GAVE_UP})


class Recorder:
    """Perekam data Fase 0 (readonly).

    Args:
        store: Persistensi tujuan.
        ws: Stream market CLOB (WSS).
        price_source: Sumber harga Chainlink BTC/USD (PriceSource).
        clock: Sumber waktu (untuk timestamp gap & time_left).
        mode: Mode operasi yang dicatat (default ``readonly``).
    """

    def __init__(
        self,
        store: Store,
        ws: ClobWS,
        price_source: PriceSource,
        clock: Clock,
        *,
        mode: str = "readonly",
    ) -> None:
        self._store = store
        self._ws = ws
        self._price_source = price_source
        self._clock = clock
        self._mode = mode
        self._pending_gaps: list[CircuitEvent] = []

    # ----- rounds & resolusi -----

    async def record_round(self, rnd: Round) -> None:
        """Persist metadata ronde (idempotent)."""
        await self._store.upsert_round(rnd)

    async def record_resolution(self, round_no: int, outcome: Outcome) -> None:
        """Catat resolusi market (status → resolved)."""
        await self._store.update_round_status(round_no, RoundStatus.RESOLVED, outcome)

    async def record_signal(self, signal: Signal) -> None:
        """Persist sinyal yang sudah dihitung (komputasi ada di Fase 1)."""
        await self._store.insert_signal(signal, mode=self._mode)

    # ----- harga (Chainlink) -----

    async def sample_price(self) -> PriceTick:
        """Baca harga BTC/USD terkini dari Chainlink (price truth)."""
        return await self._price_source.price_now()

    async def record_price_tick(self, rnd: Round) -> PriceTick:
        """Rekam tick harga + Δ (price_now - start_price) untuk satu ronde.

        Menulis baris ``signals`` berisi ``price_now``, ``delta``, ``time_left``,
        dan ``leader`` (tren berbasis Δ). Field edge (``p_win``/``ask_win``/
        ``net_edge``) di-set 0 sebagai placeholder — komputasi edge ada di Fase 1.

        Returns:
            :class:`PriceTick` yang dibaca (untuk logging Δ/staleness pemanggil).

        Raises:
            PriceUnavailableError: diteruskan dari sumber harga bila gagal.
        """
        tick = await self._price_source.price_now()
        delta = tick.price - rnd.start_price
        time_left = (rnd.window_end - self._clock.now()).total_seconds()
        if delta > 0:
            leader = "UP"
        elif delta < 0:
            leader = "DOWN"
        else:
            leader = ""
        signal = Signal(
            round_no=rnd.round_no,
            ts=tick.ts,
            price_now=tick.price,
            delta=delta,
            time_left_sec=time_left,
            p_win=Decimal(0),
            leader=leader,
            ask_win=Decimal(0),
            net_edge=Decimal(0),
        )
        await self._store.insert_signal(signal, mode=self._mode)
        return tick

    # ----- orderbook (WSS) + gap -----

    def on_circuit_event(self, event: CircuitEvent) -> None:
        """Sink event WSS (dipasang sebagai ``event_sink`` adapter).

        Non-blocking & sinkron: hanya menampung event gap untuk ditulis ke DB
        kemudian (lihat :meth:`flush_gaps`) agar tidak memblok loop trading.
        """
        if event.type in _GAP_EVENTS:
            self._pending_gaps.append(event)

    async def flush_gaps(self, round_no: int) -> int:
        """Tulis seluruh gap tertunda untuk ``round_no``; kembalikan jumlahnya."""
        count = 0
        while self._pending_gaps:
            event = self._pending_gaps.pop(0)
            await self._store.insert_gap(
                round_no,
                event.ts,
                mode=self._mode,
                detail=f"{event.type}:{event.detail}",
            )
            count += 1
        return count

    async def consume_market(
        self,
        round_no: int,
        token_ids: list[str],
        *,
        limit: int | None = None,
    ) -> int:
        """Rekam snapshot orderbook dari WSS sampai stream berhenti atau ``limit``.

        Mengembalikan jumlah snapshot orderbook yang ditulis. Gap (jika ada)
        di-flush ke DB setelah stream berakhir.
        """
        count = 0
        async for book in self._ws.stream_market(token_ids):
            await self._store.insert_book_snapshot(round_no, book, mode=self._mode)
            count += 1
            if limit is not None and count >= limit:
                break
        await self.flush_gaps(round_no)
        return count
