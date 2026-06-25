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

.. warning::
   Nama channel, format pesan subscribe, dan skema pesan WAJIB diverifikasi
   ke dokumentasi resmi Polymarket CLOB V2 (docs/04 §4.8). Konstanta di bawah
   adalah placeholder.
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
PING_PAYLOAD = json.dumps({"type": "ping"})

# Tipe alias kontrak (docs/04 §4.7).
BookUpdate = OrderBook


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


def _parse_iso_utc(value: str) -> datetime:
    """Parse ISO-8601 (termasuk sufiks ``Z``) menjadi datetime UTC aware."""
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"timestamp WSS harus tz-aware, dapat: {value!r}")
    return dt.astimezone(UTC)


def _parse_ts(value: object, clock: Clock) -> datetime:
    """Tentukan timestamp pesan; fallback ke ``clock.now()`` bila tak ada."""
    if value is None:
        return clock.now()
    if isinstance(value, bool):  # hindari int(True)
        return clock.now()
    if isinstance(value, (int, float)):
        # Asumsi epoch milidetik (TODO verify).
        return datetime.fromtimestamp(value / 1000.0, tz=UTC)
    if isinstance(value, str):
        return _parse_iso_utc(value)
    return clock.now()


def _levels(raw: object) -> list[BookLevel]:
    """Konversi list level mentah menjadi list :class:`BookLevel`."""
    if not isinstance(raw, list):
        return []
    out: list[BookLevel] = []
    for lvl in raw:
        out.append(BookLevel(price=Decimal(str(lvl["price"])), size=Decimal(str(lvl["size"]))))
    return out


def parse_book_update(obj: dict[str, Any], clock: Clock) -> OrderBook | None:
    """Petakan pesan market mentah menjadi :class:`OrderBook`.

    Mengembalikan ``None`` untuk pesan kontrol (pong/heartbeat/ack subscribe)
    atau pesan tanpa data bids/asks.

    Skema contoh yang diasumsikan (TODO verify ke docs CLOB V2)::

        {
          "event_type": "book",
          "asset_id": "111111",
          "timestamp": "2026-06-25T10:00:00Z",
          "bids": [{"price": "0.52", "size": "100"}],
          "asks": [{"price": "0.55", "size": "80"}]
        }
    """
    event_type = obj.get("event_type") or obj.get("type")
    if event_type in {"pong", "ping", "heartbeat", "subscribed", "subscribe", "ack"}:
        return None
    if "bids" not in obj and "asks" not in obj:
        return None
    token_id = obj.get("asset_id") or obj.get("token_id") or obj.get("market")
    if token_id is None:
        return None
    return OrderBook(
        token_id=str(token_id),
        ts=_parse_ts(obj.get("timestamp"), clock),
        bids=_levels(obj.get("bids", [])),
        asks=_levels(obj.get("asks", [])),
    )


class HttpClobWS:
    """Implementasi :class:`ClobWS` dengan reconnect/backoff/heartbeat/stale.

    Args:
        url: URL WSS CLOB (Settings.clob_wss_url).
        connect: Factory pembuat :class:`WSConnection` (default: websockets).
        clock: Sumber waktu (default :class:`SystemClock`).
        stale_ms: Ambang stale (ms) tanpa pesan → emit STALE + ping.
        backoff_initial: Backoff awal (detik).
        backoff_factor: Pengali backoff eksponensial.
        backoff_max: Backoff maksimum (detik).
        sleep: Fungsi tidur (injectable; default ``asyncio.sleep``).
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
        stale_ms: int = 1500,
        backoff_initial: float = 0.5,
        backoff_factor: float = 2.0,
        backoff_max: float = 30.0,
        sleep: SleepFunc = asyncio.sleep,
        event_sink: EventSink | None = None,
        max_reconnects: int | None = None,
    ) -> None:
        self._url = url
        self._connect = connect or default_connect_factory
        self._clock = clock or SystemClock()
        self._stale_sec = stale_ms / 1000.0
        self._backoff_initial = backoff_initial
        self._backoff_factor = backoff_factor
        self._backoff_max = backoff_max
        self._sleep = sleep
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
                await self._subscribe(conn, CHANNEL_MARKET, token_ids)
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
        """Loop baca pesan; deteksi stale via timeout lalu ping (heartbeat)."""
        while True:
            try:
                raw = await asyncio.wait_for(conn.recv(), timeout=self._stale_sec)
            except TimeoutError:
                self._emit(EventType.STALE, f"tidak ada pesan > {self._stale_sec}s")
                await conn.send(PING_PAYLOAD)
                continue
            obj = json.loads(raw)
            book = parse_book_update(obj, self._clock)
            if book is not None:
                yield book

    async def _subscribe(self, conn: WSConnection, channel: str, token_ids: list[str]) -> None:
        """Kirim pesan subscribe (TODO verify format resmi)."""
        msg = json.dumps({"type": "subscribe", "channel": channel, "assets_ids": token_ids})
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
    """Factory koneksi nyata menggunakan library ``websockets``."""
    ws = await websockets.connect(url)
    return _WebsocketsConnection(ws)
