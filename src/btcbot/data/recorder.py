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
    from datetime import datetime

    from btcbot.adapters.clob_ws import ClobWS
    from btcbot.adapters.clock import Clock
    from btcbot.data.store import Store
    from btcbot.domain.models import OrderBook, Outcome, PriceSource, PriceTick, Round

# Event yang menandakan data tidak kontinu → tandai gap.
_GAP_EVENTS = frozenset({EventType.DISCONNECTED, EventType.STALE, EventType.GAVE_UP})


def _best(book: OrderBook) -> tuple[Decimal | None, Decimal | None]:
    """Best bid (harga tertinggi) & best ask (harga terendah) dari book."""
    best_bid = book.bids[0].price if book.bids else None
    best_ask = book.asks[0].price if book.asks else None
    return best_bid, best_ask


class Recorder:
    """Perekam data Fase 0 (readonly).

    Retensi ``book_snapshots`` (Fase 1): order book di adapter tetap di-update
    penuh; hanya PERSISTENSI yang di-throttle agar volume tulis hemat.

    Args:
        store: Persistensi tujuan.
        ws: Stream market CLOB (WSS).
        price_source: Sumber harga Chainlink BTC/USD (PriceSource).
        clock: Sumber waktu (untuk timestamp gap, time_left, & throttle).
        mode: Mode operasi yang dicatat (default ``readonly``).
        book_persist_mode: ``"changes"`` (write-on-change + throttle) | ``"all"``.
        book_sample_ms: Throttle — maks 1 baris/token per interval ini (ms).
        book_finegrain_sec: Bila ``window_end - now <= ini`` → throttle OFF.
    """

    def __init__(  # noqa: PLR0913
        self,
        store: Store,
        ws: ClobWS,
        price_source: PriceSource,
        clock: Clock,
        *,
        mode: str = "readonly",
        book_persist_mode: str = "changes",
        book_sample_ms: int = 1000,
        book_finegrain_sec: int = 45,
    ) -> None:
        self._store = store
        self._ws = ws
        self._price_source = price_source
        self._clock = clock
        self._mode = mode
        self._persist_mode = book_persist_mode
        self._sample_ms = book_sample_ms
        self._finegrain_sec = book_finegrain_sec
        self._pending_gaps: list[CircuitEvent] = []
        # State retensi per token (untuk satu ronde): (best_bid, best_ask, ts_ms).
        self._last_persist: dict[str, tuple[Decimal | None, Decimal | None, int]] = {}

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
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> int:
        """Rekam snapshot orderbook dengan retensi write-time (lihat kelas).

        Order book di adapter tetap akurat tiap event; di sini hanya keputusan
        PERSISTENSI yang di-throttle:
        - selalu tulis bila best_bid/best_ask berubah, atau snapshot pertama;
        - bila best sama → maks 1 tulis/token per ``book_sample_ms``;
        - bila ``window_end - now <= book_finegrain_sec`` → throttle OFF;
        - snapshot TERAKHIR tiap token selalu ditulis (penanda penutup).
        Mode ``"all"`` → bypass throttle (tulis semua, regresi perilaku lama).

        Mengembalikan jumlah baris yang ditulis. ``limit`` membatasi jumlah
        update yang dikonsumsi (bukan jumlah tulis).
        """
        self._last_persist = {}  # reset state retensi per ronde
        latest: dict[str, OrderBook] = {}
        persisted_latest: dict[str, bool] = {}
        written = 0
        consumed = 0

        async for book in self._ws.stream_market(token_ids):
            consumed += 1
            now = self._clock.now()
            latest[book.token_id] = book
            if self._should_persist(book, window_end, now):
                await self._persist_book(round_no, book, now)
                persisted_latest[book.token_id] = True
                written += 1
            else:
                persisted_latest[book.token_id] = False
            if limit is not None and consumed >= limit:
                break

        # Penanda penutup: pastikan snapshot TERAKHIR tiap token tersimpan.
        for token, book in latest.items():
            if not persisted_latest.get(token, False):
                await self._persist_book(round_no, book, self._clock.now())
                written += 1

        await self.flush_gaps(round_no)
        return written

    def _should_persist(
        self,
        book: OrderBook,
        window_end: datetime | None,
        now: datetime,
    ) -> bool:
        """Putuskan apakah ``book`` perlu ditulis (aturan retensi)."""
        if self._persist_mode == "all":
            return True
        last = self._last_persist.get(book.token_id)
        if last is None:
            return True  # snapshot pertama token ini di ronde
        best_bid, best_ask = _best(book)
        last_bid, last_ask, last_ms = last
        if best_bid != last_bid or best_ask != last_ask:
            return True  # perubahan harga = sinyal penting, jangan di-drop
        if window_end is not None and (window_end - now).total_seconds() <= self._finegrain_sec:
            return True  # fine-grain akhir-window → resolusi penuh
        now_ms = int(now.timestamp() * 1000)
        return (now_ms - last_ms) >= self._sample_ms  # throttle

    async def _persist_book(self, round_no: int, book: OrderBook, now: datetime) -> None:
        """Tulis snapshot & perbarui state retensi token."""
        await self._store.insert_book_snapshot(round_no, book, mode=self._mode)
        best_bid, best_ask = _best(book)
        self._last_persist[book.token_id] = (best_bid, best_ask, int(now.timestamp() * 1000))
