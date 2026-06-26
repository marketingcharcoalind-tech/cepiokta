"""Unit tests for btcbot.adapters.clob_ws (deterministic fake WebSocket)."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from btcbot.adapters.clob_ws import (
    BookState,
    CircuitEvent,
    EventType,
    HttpClobWS,
    WSConnection,
    WSConnectionClosedError,
    default_connect_factory,
    market_channel_url,
    parse_ws_element,
)
from btcbot.adapters.clock import SimClock
from btcbot.domain.models import OrderBook

# Token id dari fixture capture asli (UP=TOKEN1, DOWN=TOKEN2 sesuai urutan subscribe).
TOKEN1 = "77079965253543126197839710562568101379562196608086107097307885100759018795513"
TOKEN2 = "68703425550147798755310404059926816236727564659155250617449891670396842729843"
WS_FIXTURE = Path(__file__).parent.parent / "fixtures" / "ws_market_capture.json"


def _frames() -> list[Any]:
    return cast("list[Any]", json.loads(WS_FIXTURE.read_text(encoding="utf-8")))


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


# --- channel url ---


class TestMarketChannelUrl:
    def test_appends_market_to_ws(self) -> None:
        assert market_channel_url("wss://x/ws") == "wss://x/ws/market"

    def test_keeps_market(self) -> None:
        assert market_channel_url("wss://x/ws/market") == "wss://x/ws/market"

    def test_strips_trailing_slash(self) -> None:
        assert market_channel_url("wss://x/ws/market/") == "wss://x/ws/market"


# --- parse tests (fixture asli) ---


class TestParseWsElement:
    def test_list_snapshot_two_tokens(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        state = BookState()
        snapshot = _frames()[0]  # list 2 token
        books: list[OrderBook] = []
        for elem in snapshot:
            books += parse_ws_element(elem, state, clock)
        assert len(books) == 2
        by_token = {b.token_id: b for b in books}
        assert set(by_token) == {TOKEN1, TOKEN2}
        # best_bid = harga tertinggi bids; best_ask = harga terendah asks.
        b1 = by_token[TOKEN1]
        assert b1.bids[0].price == Decimal("0.97")  # best bid
        assert b1.asks[0].price == Decimal("0.98")  # best ask
        b2 = by_token[TOKEN2]
        assert b2.bids[0].price == Decimal("0.02")
        assert b2.asks[0].price == Decimal("0.03")

    def test_snapshot_timestamp_ms_to_utc(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        state = BookState()
        book = parse_ws_element(_frames()[0][0], state, clock)[0]
        assert book.ts == datetime.fromtimestamp(1782478014034 / 1000.0, tz=UTC)
        assert book.ts.tzinfo is not None

    def test_bids_sorted_desc_asks_asc(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        state = BookState()
        book = parse_ws_element(_frames()[0][0], state, clock)[0]
        bid_prices = [lvl.price for lvl in book.bids]
        ask_prices = [lvl.price for lvl in book.asks]
        assert bid_prices == sorted(bid_prices, reverse=True)
        assert ask_prices == sorted(ask_prices)

    def test_price_change_updates_best(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        state = BookState()
        for elem in _frames()[0]:
            parse_ws_element(elem, state, clock)
        # frame[1] = price_change (token2 BUY 0.02, token1 SELL 0.98)
        books = parse_ws_element(_frames()[1], state, clock)
        assert len(books) == 2
        by_token = {b.token_id: b for b in books}
        # token2 BUY 0.02 → bid level ada; best bid token2 tetap 0.02
        assert by_token[TOKEN2].bids[0].price == Decimal("0.02")
        # token1 SELL 0.98 → ask level 0.98 ter-update; best ask token1 = 0.98
        assert by_token[TOKEN1].asks[0].price == Decimal("0.98")

    def test_size_zero_removes_level(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        state = BookState()
        parse_ws_element(_frames()[0][0], state, clock)  # snapshot token1, best bid 0.97
        change = {
            "event_type": "price_change",
            "market": "0xabc",
            "timestamp": "1782478014100",
            "price_changes": [{"asset_id": TOKEN1, "price": "0.97", "size": "0", "side": "BUY"}],
        }
        book = parse_ws_element(change, state, clock)[0]
        prices = {lvl.price for lvl in book.bids}
        assert Decimal("0.97") not in prices  # level dihapus
        assert book.bids[0].price == Decimal("0.96")  # best bid turun

    def test_unknown_event_type_skipped(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        state = BookState()
        for et in ("tick_size_change", "last_trade_price", "pong", "subscribed"):
            assert parse_ws_element({"event_type": et}, state, clock) == []

    def test_snapshot_without_asset_id_skipped(self) -> None:
        clock = SimClock(datetime(2026, 1, 1, tzinfo=UTC))
        state = BookState()
        assert parse_ws_element({"bids": [], "asks": []}, state, clock) == []

    def test_dict_book_event(self) -> None:
        clock = SimClock(datetime(2026, 5, 5, 12, 0, tzinfo=UTC))
        state = BookState()
        book = parse_ws_element(json.loads(_book_msg("111")), state, clock)[0]
        assert book.token_id == "111"
        assert book.bids[0].price == Decimal("0.52")


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
        # subscribe terkirim dengan format resmi {"assets_ids":[...],"type":"market"}
        assert len(conn.sent) >= 1
        sub = json.loads(conn.sent[0])
        assert sub == {"assets_ids": ["111", "222"], "type": "market"}

    async def test_stream_handles_list_snapshot(self) -> None:
        # Pesan WS berupa LIST (book snapshot 2 token) tidak boleh crash.
        msg = json.dumps(_frames()[0])
        conn = FakeConnection([("msg", msg), ("close",)])
        ws = HttpClobWS(
            "wss://x",
            connect=_factory_from([conn]),
            clock=SimClock(datetime(2026, 1, 1, tzinfo=UTC)),
            sleep=_no_sleep,
            max_reconnects=0,
        )
        books = [b async for b in ws.stream_market([TOKEN1, TOKEN2])]
        assert {b.token_id for b in books} == {TOKEN1, TOKEN2}
        by_token = {b.token_id: b for b in books}
        assert by_token[TOKEN1].asks[0].price == Decimal("0.98")

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

    async def test_stale_detection_triggers_reconnect(self) -> None:
        events: list[CircuitEvent] = []
        # stall memicu timeout (stale) → koneksi dianggap mati → reconnect.
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
        types = [e.type for e in events]
        assert EventType.STALE in types
        # stale → keluar dari read loop → reconnect berikutnya menyerah (GAVE_UP)
        assert EventType.GAVE_UP in types

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


class TestHeartbeat:
    async def test_sends_ping_at_interval(self) -> None:
        conn = FakeConnection([])
        calls = {"n": 0}

        async def fake_ping_sleep(_s: float) -> None:
            calls["n"] += 1
            if calls["n"] > 3:
                raise asyncio.CancelledError

        ws = HttpClobWS("wss://x", connect=_factory_from([conn]), ping_sleep=fake_ping_sleep)
        with pytest.raises(asyncio.CancelledError):
            await ws._heartbeat(conn)
        assert conn.sent.count("PING") == 3

    async def test_heartbeat_stops_on_closed_connection(self) -> None:
        class ClosingConn:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send(self, message: str) -> None:
                raise WSConnectionClosedError("closed")

            async def recv(self) -> str:
                raise WSConnectionClosedError("closed")

            async def close(self) -> None:
                return None

        async def instant_sleep(_s: float) -> None:
            return None

        ws = HttpClobWS("wss://x", ping_sleep=instant_sleep)
        # Tidak boleh raise (ditelan diam-diam) saat koneksi putus.
        await ws._heartbeat(ClosingConn())


class TestConnectFactory:
    async def test_disables_library_keepalive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        class FakeWS:
            async def recv(self) -> str:
                return ""

            async def send(self, _m: str) -> None:
                return None

            async def close(self) -> None:
                return None

        async def fake_connect(url: str, **kwargs: object) -> FakeWS:
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeWS()

        monkeypatch.setattr("btcbot.adapters.clob_ws.websockets.connect", fake_connect)
        await default_connect_factory("wss://x/ws/market")
        kwargs = cast("dict[str, object]", captured["kwargs"])
        assert kwargs["ping_interval"] is None
        assert kwargs["ping_timeout"] is None
        assert kwargs["max_size"] is None
        assert kwargs["open_timeout"] == 10
        assert kwargs["close_timeout"] == 5


class TestNonJsonFrames:
    async def test_pong_text_frame_skipped(self) -> None:
        # Frame "PONG"/"PING" (non-JSON) tidak boleh crash; book setelahnya tetap diparse.
        conn = FakeConnection(
            [("msg", "PONG"), ("msg", "PING"), ("msg", _book_msg("111")), ("close",)]
        )
        ws = HttpClobWS(
            "wss://x",
            connect=_factory_from([conn]),
            clock=SimClock(datetime(2026, 1, 1, tzinfo=UTC)),
            sleep=_no_sleep,
            max_reconnects=0,
        )
        books = [b async for b in ws.stream_market(["111"])]
        assert [b.token_id for b in books] == ["111"]
