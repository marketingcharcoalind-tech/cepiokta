"""Unit tests for btcbot.adapters.clob_ws (deterministic fake WebSocket)."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from btcbot.adapters.clob_ws import (
    CircuitEvent,
    EventType,
    HttpClobWS,
    WSConnection,
    WSConnectionClosedError,
    parse_book_update,
)
from btcbot.adapters.clock import SimClock
from btcbot.domain.models import OrderBook

# --- helpers / fakes ---


def _book_msg(token_id: str = "111", ts: str = "2026-06-25T10:00:00Z") -> str:
    return json.dumps(
        {
            "event_type": "book",
            "asset_id": token_id,
            "timestamp": ts,
            "bids": [{"price": "0.52", "size": "100"}],
            "asks": [{"price": "0.55", "size": "80"}],
        }
    )


class FakeConnection:
    """Koneksi WS palsu yang menjalankan script aksi secara deterministik.

    Script berisi tuple aksi:
    - ("msg", payload)  -> recv() mengembalikan payload
    - ("close",)        -> recv() raise WSConnectionClosed
    - ("stall",)        -> recv() menunggu lama (memicu timeout/stale)
    """

    def __init__(self, script: list[tuple[str, ...]]) -> None:
        self._script = list(script)
        self._idx = 0
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self._idx >= len(self._script):
            raise WSConnectionClosedError("script habis")
        action = self._script[self._idx]
        self._idx += 1
        kind = action[0]
        if kind == "msg":
            return action[1]
        if kind == "close":
            raise WSConnectionClosedError("disconnect terjadwal")
        if kind == "stall":
            await asyncio.sleep(1.0)  # akan di-cancel oleh wait_for (timeout stale)
            return ""
        raise AssertionError(f"aksi tak dikenal: {kind}")

    async def close(self) -> None:
        self.closed = True


def _factory_from(conns: list[FakeConnection]) -> Callable[[str], Awaitable[WSConnection]]:
    """Buat connect factory yang mengembalikan koneksi berurutan."""
    seq = iter(conns)

    async def factory(_url: str) -> WSConnection:
        try:
            return next(seq)
        except StopIteration as exc:
            raise WSConnectionClosedError("tidak ada koneksi tersisa") from exc

    return factory


async def _no_sleep(_seconds: float) -> None:
    """Pengganti asyncio.sleep agar backoff tidak menunda test."""


# --- parse tests ---


class TestParseBookUpdate:
    def test_parses_book(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        book = parse_book_update(json.loads(_book_msg()), clock)
        assert isinstance(book, OrderBook)
        assert book.token_id == "111"
        assert book.ts == datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
        assert book.bids[0].price == Decimal("0.52")
        assert book.asks[0].size == Decimal("80")

    def test_control_messages_ignored(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        for ctrl in ({"type": "pong"}, {"type": "subscribed"}, {"event_type": "heartbeat"}):
            assert parse_book_update(ctrl, clock) is None

    def test_timestamp_fallback_to_clock(self) -> None:
        clock = SimClock(datetime(2026, 5, 5, 12, 0, tzinfo=UTC))
        obj = {"event_type": "book", "asset_id": "1", "bids": [], "asks": []}
        book = parse_book_update(obj, clock)
        assert book is not None
        assert book.ts == datetime(2026, 5, 5, 12, 0, tzinfo=UTC)

    def test_epoch_ms_timestamp(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        obj = {"event_type": "book", "asset_id": "1", "timestamp": 1750000000000, "bids": []}
        book = parse_book_update(obj, clock)
        assert book is not None
        assert book.ts.tzinfo is not None


# --- stream tests ---


class TestStreamMarket:
    async def test_yields_parsed_updates(self) -> None:
        conn = FakeConnection([("msg", _book_msg("111")), ("msg", _book_msg("222")), ("close",)])
        ws = HttpClobWS(
            "wss://x",
            connect=_factory_from([conn]),
            clock=SimClock(datetime(2026, 1, 1, tzinfo=UTC)),
            sleep=_no_sleep,
            max_reconnects=0,
        )
        books = [b async for b in ws.stream_market(["111", "222"])]
        assert [b.token_id for b in books] == ["111", "222"]
        # subscribe terkirim
        assert any("subscribe" in s for s in conn.sent)

    async def test_disconnect_then_reconnect(self) -> None:
        events: list[CircuitEvent] = []
        conn1 = FakeConnection([("msg", _book_msg("111")), ("close",)])
        conn2 = FakeConnection([("msg", _book_msg("222")), ("close",)])
        ws = HttpClobWS(
            "wss://x",
            connect=_factory_from([conn1, conn2]),
            clock=SimClock(datetime(2026, 1, 1, tzinfo=UTC)),
            sleep=_no_sleep,
            event_sink=events.append,
            max_reconnects=1,
        )
        books = [b async for b in ws.stream_market(["111"])]
        assert [b.token_id for b in books] == ["111", "222"]
        types = [e.type for e in events]
        assert EventType.CONNECTED in types
        assert EventType.DISCONNECTED in types
        assert EventType.RECONNECTED in types
        assert EventType.GAVE_UP in types

    async def test_backoff_is_exponential(self) -> None:
        slept: list[float] = []

        async def record_sleep(seconds: float) -> None:
            slept.append(seconds)

        conns = [FakeConnection([("close",)]) for _ in range(3)]
        ws = HttpClobWS(
            "wss://x",
            connect=_factory_from(conns),
            clock=SimClock(datetime(2026, 1, 1, tzinfo=UTC)),
            sleep=record_sleep,
            backoff_initial=0.5,
            backoff_factor=2.0,
            max_reconnects=2,
        )
        _ = [b async for b in ws.stream_market(["111"])]
        # 0.5 -> 1.0 untuk dua kali backoff sebelum menyerah
        assert slept[:2] == [0.5, 1.0]

    async def test_stale_detection_emits_event_and_pings(self) -> None:
        events: list[CircuitEvent] = []
        # stall memicu timeout (stale), lalu close untuk mengakhiri.
        conn = FakeConnection([("stall",), ("close",)])
        ws = HttpClobWS(
            "wss://x",
            connect=_factory_from([conn]),
            clock=SimClock(datetime(2026, 1, 1, tzinfo=UTC)),
            sleep=_no_sleep,
            stale_ms=20,
            event_sink=events.append,
            max_reconnects=0,
        )
        _ = [b async for b in ws.stream_market(["111"])]
        assert EventType.STALE in [e.type for e in events]
        # heartbeat ping dikirim saat stale
        assert any("ping" in s for s in conn.sent)

    async def test_connect_failure_then_give_up(self) -> None:
        events: list[CircuitEvent] = []

        async def failing_factory(_url: str) -> WSConnection:
            raise WSConnectionClosedError("gagal konek")

        ws = HttpClobWS(
            "wss://x",
            connect=failing_factory,
            clock=SimClock(datetime(2026, 1, 1, tzinfo=UTC)),
            sleep=_no_sleep,
            event_sink=events.append,
            max_reconnects=0,
        )
        books = [b async for b in ws.stream_market(["111"])]
        assert books == []
        assert EventType.GAVE_UP in [e.type for e in events]


class TestStreamUser:
    async def test_stream_user_is_stub(self) -> None:
        ws = HttpClobWS("wss://x", connect=_factory_from([]))
        with pytest.raises(NotImplementedError):
            _ = [u async for u in ws.stream_user()]
