"""CLOB WebSocket adapter — realtime market & user streams (docs/04 §4.4, docs/08 §8.4).

Menyediakan :class:`ClobWS` (Protocol) dan implementasi :class:`HttpClobWS`
yang men-stream orderbook (channel ``market``) dan order/fill kita (channel
``user``, distub untuk Fase 2/3).

Fitur ketahanan (docs/04 §4.4, docs/06 §6.4):
- **Reconnect** dengan *exponential backoff*.
- **Heartbeat/ping** saat idle.
- **Deteksi stale**: bila tidak ada pesan > ``STALE_MS`` → emit
  :class:`CircuitEvent` (dikonsumsi circuit breaker di Risk Manager).
- Parsing pesan → domain :class:`~btcbot.domain.models.OrderBook`.

Koneksi WebSocket diabstraksi di balik :class:`WSConnection` + factory agar
dapat di-mock secara deterministik saat test (tanpa jaringan nyata).

.. note::
   Skema pesan market diverifikasi dari capture **live** (VPS jaringan-bersih):
   - Book snapshot = JSON **array** (satu objek per token, ada ``bids``/``asks``).
   - ``price_change`` = JSON **object** (``price_changes[]``: side BUY→bid,
     SELL→ask; size 0 ⇒ hapus level). Lihat ``tests/fixtures/ws_market_capture.json``.
   Endpoint: ``wss://.../ws/market`` (path WAJIB ``/market``, tanpa trailing slash).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

import websockets

from btcbot.adapters.clock import Clock, SystemClock
from btcbot.domain.models import BookLevel, OrderBook

if TYPE_CHECKING:
    from types import TracebackType

# TODO(verify): konfirmasi nama channel & format subscribe ke docs CLOB V2.
CHANNEL_MARKET = "market"
CHANNEL_USER = "user"
# Heartbeat aplikasi-level: server CLOB mengharap teks "PING" → balas "PONG".
# (Keepalive protokol-level library dimatikan; lihat default_connect_factory.)
PING_PAYLOAD = "PING"

# Tipe alias kontrak (docs/04 §4.7).
BookUpdate = OrderBook


def market_channel_url(base_url: str) -> str:
    """Pastikan URL berakhir di channel ``/market``.

    - ``.../ws/market`` → tetap.
    - ``.../ws``        → ``.../ws/market``.
    - selain itu        → apa adanya (mis. URL test).

    Catatan endpoint live: path WAJIB ``/ws/market`` (TANPA trailing slash);
    ``/ws`` & ``/ws/market/`` → 404.
    """
    url = base_url.rstrip("/")
    if url.endswith("/market"):
        return url
    if url.endswith("/ws"):
        return url + "/market"
    return url


@dataclass(frozen=True, slots=True)
class UserUpdate:
    """Update channel ``user`` (order/fill milik kita). Stub Fase 2/3."""

    raw: dict[str, Any]


class WSConnectionClosedError(Exception):
    """Diangkat oleh :class:`WSConnection` saat koneksi tertutup/putus."""


class WSConnection(Protocol):
    """Abstraksi koneksi WebSocket tunggal (mock-able)."""

    async def send(self, message: str) -> None:
        """Kirim pesan teks."""
        ...

    async def recv(self) -> str:
        """Terima satu pesan teks. Raise :class:`WSConnectionClosedError` bila putus."""
        ...

    async def close(self) -> None:
        """Tutup koneksi."""
        ...


WSConnectFactory = Callable[[str], Awaitable[WSConnection]]
SleepFunc = Callable[[float], Awaitable[None]]


class EventType(StrEnum):
    """Jenis event ketahanan koneksi (untuk circuit breaker)."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTED = "reconnected"
    STALE = "stale"
    GAVE_UP = "gave_up"


@dataclass(frozen=True, slots=True)
class CircuitEvent:
    """Event yang diemit ke circuit breaker (docs/06 §6.4)."""

    type: EventType
    ts: datetime
    detail: str = ""


EventSink = Callable[[CircuitEvent], None]


class ClobWS(Protocol):
    """Kontrak stream realtime CLOB (docs/08 §8.4)."""

    def stream_market(self, token_ids: list[str]) -> AsyncIterator[BookUpdate]:
        """Stream update orderbook untuk daftar token (UP & DOWN)."""
        ...

    def stream_user(self) -> AsyncIterator[UserUpdate]:
        """Stream order/fill milik kita (terotentikasi)."""
        ...


def _parse_ms_ts(value: object, clock: Clock) -> datetime:
    """Parse timestamp WS (epoch milidetik string/number) → datetime UTC.

    Fallback ke ``clock.now()`` bila tak ada / tak terbaca.
    """
    if value is None or isinstance(value, bool):
        return clock.now()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=UTC)
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            return datetime.fromtimestamp(int(s) / 1000.0, tz=UTC)
        normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return clock.now()
        return dt.astimezone(UTC) if dt.tzinfo else clock.now()
    return clock.now()


# Event control yang di-skip aman (bukan book/price_change).
_CONTROL_EVENTS = frozenset(
    {
        "pong",
        "ping",
        "heartbeat",
        "subscribed",
        "subscribe",
        "ack",
        "tick_size_change",
        "last_trade_price",
    }
)


@dataclass
class _AssetBook:
    """State order book satu asset (price→size untuk bids & asks)."""

    market: str = ""
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)


class BookState:
    """Order book yang dipertahankan PER ``asset_id`` lintas pesan WS.

    - Snapshot (objek dgn ``bids``/``asks``) → reset book asset tsb.
    - ``price_change`` entry → update level (BUY=bids, SELL=asks); size 0 hapus.
    """

    def __init__(self) -> None:
        self._books: dict[str, _AssetBook] = {}

    def apply_snapshot(self, elem: dict[str, Any]) -> str:
        """Reset book dari snapshot; kembalikan ``asset_id``."""
        asset_id = str(elem["asset_id"])
        book = _AssetBook(market=str(elem.get("market", "")))
        for lvl in elem.get("bids") or []:
            price = Decimal(str(lvl["price"]))
            size = Decimal(str(lvl["size"]))
            if size > 0:
                book.bids[price] = size
        for lvl in elem.get("asks") or []:
            price = Decimal(str(lvl["price"]))
            size = Decimal(str(lvl["size"]))
            if size > 0:
                book.asks[price] = size
        self._books[asset_id] = book
        return asset_id

    def apply_change(self, market: str, entry: dict[str, Any]) -> str:
        """Terapkan satu entry ``price_change``; kembalikan ``asset_id``."""
        asset_id = str(entry["asset_id"])
        book = self._books.get(asset_id)
        if book is None:
            book = _AssetBook(market=market)
            self._books[asset_id] = book
        side = str(entry["side"]).upper()
        price = Decimal(str(entry["price"]))
        size = Decimal(str(entry["size"]))
        levels = book.bids if side == "BUY" else book.asks
        if size == 0:
            levels.pop(price, None)
        else:
            levels[price] = size
        return asset_id

    def to_orderbook(self, asset_id: str, ts: datetime) -> OrderBook:
        """Bangun :class:`OrderBook` (bids desc, asks asc — best di index 0)."""
        book = self._books[asset_id]
        bids = [
            BookLevel(price=p, size=s)
            for p, s in sorted(book.bids.items(), key=lambda kv: kv[0], reverse=True)
        ]
        asks = [
            BookLevel(price=p, size=s) for p, s in sorted(book.asks.items(), key=lambda kv: kv[0])
        ]
        return OrderBook(token_id=asset_id, ts=ts, bids=bids, asks=asks)


def parse_ws_element(elem: dict[str, Any], state: BookState, clock: Clock) -> list[OrderBook]:
    """Proses SATU elemen pesan WS market → daftar :class:`OrderBook`.

    - Snapshot (ada ``bids``/``asks`` atau ``event_type=="book"``) → 1 book.
    - ``price_change`` → 1 book per entry (asset terdampak).
    - event lain / kontrol → ``[]`` (di-skip aman, tidak crash).

    Mengubah ``state`` (book per asset). Harga/size memakai :class:`Decimal`.
    """
    event_type = elem.get("event_type")
    if event_type in _CONTROL_EVENTS:
        return []

    if "bids" in elem or "asks" in elem or event_type == "book":
        if "asset_id" not in elem or elem.get("asset_id") is None:
            return []
        asset_id = state.apply_snapshot(elem)
        ts = _parse_ms_ts(elem.get("timestamp"), clock)
        return [state.to_orderbook(asset_id, ts)]

    if event_type == "price_change":
        market = str(elem.get("market", ""))
        ts = _parse_ms_ts(elem.get("timestamp"), clock)
        out: list[OrderBook] = []
        for entry in elem.get("price_changes") or []:
            if not isinstance(entry, dict) or "asset_id" not in entry:
                continue
            asset_id = state.apply_change(market, entry)
            out.append(state.to_orderbook(asset_id, ts))
        return out

    # event_type tak dikenal → skip aman.
    return []


class HttpClobWS:
    """Implementasi :class:`ClobWS` dengan reconnect/backoff/heartbeat/stale.

    Args:
        url: URL WSS CLOB (Settings.clob_wss_url).
        connect: Factory pembuat :class:`WSConnection` (default: websockets).
        clock: Sumber waktu (default :class:`SystemClock`).
        stale_ms: Ambang stale (ms) tanpa pesan → tutup & reconnect.
        app_ping_seconds: Interval heartbeat aplikasi-level (kirim PING).
        backoff_initial: Backoff awal (detik).
        backoff_factor: Pengali backoff eksponensial.
        backoff_max: Backoff maksimum (detik).
        sleep: Fungsi tidur backoff (injectable; default ``asyncio.sleep``).
        ping_sleep: Fungsi tidur heartbeat (injectable; default ``asyncio.sleep``).
        event_sink: Callback penerima :class:`CircuitEvent` (untuk circuit breaker).
        max_reconnects: Batas reconnect berturut sebelum menyerah (None = tak
            terbatas; berguna untuk test agar stream berakhir).
    """

    def __init__(  # noqa: PLR0913
        self,
        url: str,
        *,
        connect: WSConnectFactory | None = None,
        clock: Clock | None = None,
        stale_ms: int = 30_000,
        app_ping_seconds: float = 10.0,
        backoff_initial: float = 0.5,
        backoff_factor: float = 2.0,
        backoff_max: float = 30.0,
        sleep: SleepFunc = asyncio.sleep,
        ping_sleep: SleepFunc = asyncio.sleep,
        event_sink: EventSink | None = None,
        max_reconnects: int | None = None,
    ) -> None:
        self._url = market_channel_url(url)
        self._connect = connect or default_connect_factory
        self._clock = clock or SystemClock()
        self._stale_sec = stale_ms / 1000.0
        self._app_ping_sec = app_ping_seconds
        self._backoff_initial = backoff_initial
        self._backoff_factor = backoff_factor
        self._backoff_max = backoff_max
        self._sleep = sleep
        self._ping_sleep = ping_sleep
        self._event_sink = event_sink
        self._max_reconnects = max_reconnects
        self._closed = False

    # ----- public API -----

    async def stream_market(self, token_ids: list[str]) -> AsyncIterator[BookUpdate]:
        """Stream update orderbook dengan reconnect & deteksi stale."""
        attempts = 0
        first = True
        backoff = self._backoff_initial

        while not self._closed:
            try:
                conn = await self._connect(self._url)
            except (WSConnectionClosedError, OSError) as exc:
                attempts += 1
                self._emit(EventType.DISCONNECTED, f"connect gagal: {exc}")
                if self._gave_up(attempts):
                    return
                await self._sleep(backoff)
                backoff = self._next_backoff(backoff)
                continue

            self._emit(EventType.CONNECTED if first else EventType.RECONNECTED)
            first = False

            try:
                await self._subscribe(conn, token_ids)
                async for book in self._read_market(conn):
                    # Koneksi terbukti sehat: reset penghitung & backoff.
                    attempts = 0
                    backoff = self._backoff_initial
                    yield book
            except (WSConnectionClosedError, OSError) as exc:
                self._emit(EventType.DISCONNECTED, str(exc))
            finally:
                await self._safe_close(conn)

            attempts += 1
            if self._gave_up(attempts):
                return
            await self._sleep(backoff)
            backoff = self._next_backoff(backoff)

    async def stream_user(self) -> AsyncIterator[UserUpdate]:
        """Stub channel user (diimplementasikan Fase 2/3)."""
        raise NotImplementedError("stream_user diimplementasikan pada Fase 2/3")
        # Baris berikut tak pernah tereksekusi; membuat fungsi ini async generator
        # sehingga sesuai kontrak Protocol (dapat dipakai via `async for`).
        yield UserUpdate(raw={})  # type: ignore[unreachable]

    def close(self) -> None:
        """Tandai stream agar berhenti pada iterasi berikutnya."""
        self._closed = True

    def set_event_sink(self, sink: EventSink | None) -> None:
        """Pasang/ganti sink :class:`CircuitEvent` (mis. recorder gap-sink).

        Berguna untuk wiring setelah konstruksi agar tidak terjadi dependensi
        melingkar antara WS dan konsumernya.
        """
        self._event_sink = sink

    # ----- internals -----

    async def _read_market(self, conn: WSConnection) -> AsyncIterator[OrderBook]:
        """Loop baca pesan + heartbeat aplikasi-level + deteksi stale.

        Pesan bisa LIST (book snapshot per token) atau DICT (price_change / event
        lain). Frame non-JSON (mis. "PONG"/"PING") di-skip aman. Heartbeat
        dikirim oleh task terpisah (:meth:`_heartbeat`) agar koneksi tetap hidup
        meski data mengalir terus. Jika tak ada pesan > ``stale_sec`` → koneksi
        dianggap mati → keluar (memicu reconnect dengan backoff).
        """
        state = BookState()
        hb_task = asyncio.create_task(self._heartbeat(conn))
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(conn.recv(), timeout=self._stale_sec)
                except TimeoutError:
                    self._emit(EventType.STALE, f"tidak ada pesan > {self._stale_sec}s")
                    return
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    # Frame non-JSON (mis. balasan "PONG"/"PING") → skip aman.
                    continue
                elements = obj if isinstance(obj, list) else [obj]
                for elem in elements:
                    if not isinstance(elem, dict):
                        continue
                    for book in parse_ws_element(elem, state, self._clock):
                        yield book
        finally:
            hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb_task

    async def _heartbeat(self, conn: WSConnection) -> None:
        """Kirim ``PING`` tiap ``app_ping_seconds`` (heartbeat aplikasi-level).

        Wajib karena keepalive protokol-level library dimatikan (server CLOB
        tidak membalas ping protokol). Berhenti diam-diam saat koneksi putus;
        dibatalkan oleh :meth:`_read_market` ketika stream berakhir.
        """
        with contextlib.suppress(WSConnectionClosedError, OSError):
            while True:
                await self._ping_sleep(self._app_ping_sec)
                await conn.send(PING_PAYLOAD)

    async def _subscribe(self, conn: WSConnection, token_ids: list[str]) -> None:
        """Kirim pesan subscribe channel market (format resmi CLOB)."""
        msg = json.dumps({"assets_ids": token_ids, "type": CHANNEL_MARKET})
        await conn.send(msg)

    def _emit(self, event_type: EventType, detail: str = "") -> None:
        """Emit :class:`CircuitEvent` ke sink (jika ada)."""
        if self._event_sink is not None:
            self._event_sink(CircuitEvent(type=event_type, ts=self._clock.now(), detail=detail))

    def _gave_up(self, attempts: int) -> bool:
        """True bila batas reconnect terlampaui (emit GAVE_UP)."""
        if self._max_reconnects is not None and attempts > self._max_reconnects:
            self._emit(EventType.GAVE_UP, f"menyerah setelah {attempts} percobaan")
            return True
        return False

    def _next_backoff(self, current: float) -> float:
        """Hitung backoff berikutnya (eksponensial, dibatasi max)."""
        return min(current * self._backoff_factor, self._backoff_max)

    @staticmethod
    async def _safe_close(conn: WSConnection) -> None:
        """Tutup koneksi tanpa melempar error."""
        with contextlib.suppress(WSConnectionClosedError, OSError):
            await conn.close()

    async def __aenter__(self) -> HttpClobWS:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


# --- default factory berbasis library `websockets` (boundary I/O nyata) ---


@dataclass
class _WebsocketsConnection:
    """Adapter tipis dari koneksi `websockets` ke :class:`WSConnection`."""

    ws: Any
    _closed: bool = field(default=False)

    async def send(self, message: str) -> None:
        await self.ws.send(message)

    async def recv(self) -> str:
        try:
            data = await self.ws.recv()
        except websockets.ConnectionClosed as exc:
            raise WSConnectionClosedError(str(exc)) from exc
        return data if isinstance(data, str) else data.decode()

    async def close(self) -> None:
        await self.ws.close()


async def default_connect_factory(url: str) -> WSConnection:
    """Factory koneksi nyata via ``websockets`` (keepalive library DIMATIKAN).

    Server CLOB Polymarket tidak membalas ping protokol-level → set
    ``ping_interval=None``/``ping_timeout=None`` agar library tidak menutup
    koneksi (1011) meski data mengalir. Keepalive dijaga heartbeat aplikasi
    (kirim "PING"). Library tetap AUTO-membalas ping yang DITERIMA dari server.
    """
    ws = await websockets.connect(
        url,
        ping_interval=None,
        ping_timeout=None,
        close_timeout=5,
        open_timeout=10,
        max_size=None,  # book snapshot bisa besar
    )
    return _WebsocketsConnection(ws)
