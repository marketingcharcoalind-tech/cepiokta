"""Unit tests for btcbot.data.recorder (integration with in-memory store)."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from btcbot.adapters.chainlink import FakePriceSource
from btcbot.adapters.clob_ws import (
    CircuitEvent,
    EventType,
    HttpClobWS,
    WSConnection,
    WSConnectionClosedError,
)
from btcbot.adapters.clock import SimClock
from btcbot.data.recorder import Recorder
from btcbot.data.store import Store
from btcbot.domain.models import Outcome, Round, RoundStatus

WS = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
WE = datetime(2026, 6, 25, 10, 5, tzinfo=UTC)


@pytest.fixture
async def store() -> AsyncIterator[Store]:
    s = await Store.open("sqlite+aiosqlite:///:memory:")
    try:
        yield s
    finally:
        await s.close()


def _round() -> Round:
    return Round(
        condition_id="0xabc",
        round_no=48247,
        token_id_up="111",
        token_id_down="222",
        window_start=WS,
        window_end=WE,
        start_price=Decimal("64250.50"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        status=RoundStatus.ACTIVE,
    )


def _book_msg(token_id: str = "111") -> str:
    return json.dumps(
        {
            "event_type": "book",
            "asset_id": token_id,
            "timestamp": "2026-06-25T10:00:00Z",
            "bids": [{"price": "0.52", "size": "100"}],
            "asks": [{"price": "0.55", "size": "80"}],
        }
    )


class FakeConnection:
    def __init__(self, script: list[tuple[str, ...]]) -> None:
        self._script = list(script)
        self._idx = 0

    async def send(self, message: str) -> None:
        return None

    async def recv(self) -> str:
        if self._idx >= len(self._script):
            raise WSConnectionClosedError("habis")
        action = self._script[self._idx]
        self._idx += 1
        if action[0] == "msg":
            return action[1]
        raise WSConnectionClosedError("disconnect terjadwal")

    async def close(self) -> None:
        return None


def _single_conn_factory(conn: FakeConnection) -> Callable[[str], Awaitable[WSConnection]]:
    used = False

    async def factory(_url: str) -> WSConnection:
        nonlocal used
        if used:
            raise WSConnectionClosedError("habis")
        used = True
        return conn

    return factory


async def _no_sleep(_seconds: float) -> None:
    return None


class TestRecorderRounds:
    async def test_record_round(self, store: Store) -> None:
        feed = FakePriceSource(Decimal("64000"))
        ws = HttpClobWS("wss://x")
        rec = Recorder(store, ws, feed, SimClock(WS), mode="readonly")
        await rec.record_round(_round())
        got = await store.get_round(48247)
        assert got is not None
        assert got.round_no == 48247

    async def test_record_resolution(self, store: Store) -> None:
        feed = FakePriceSource(Decimal("64000"))
        rec = Recorder(store, HttpClobWS("wss://x"), feed, SimClock(WS))
        await rec.record_round(_round())
        await rec.record_resolution(48247, Outcome.UP)
        got = await store.get_round(48247)
        assert got is not None
        assert got.status is RoundStatus.RESOLVED
        assert got.resolved_outcome is Outcome.UP


class TestRecorderPrice:
    async def test_sample_price_returns_tick(self, store: Store) -> None:
        feed = FakePriceSource(Decimal("64321.5"))
        rec = Recorder(store, HttpClobWS("wss://x"), feed, SimClock(WS))
        tick = await rec.sample_price()
        assert tick.price == Decimal("64321.5")

    async def test_record_price_tick_writes_delta(self, store: Store) -> None:
        # start_price 64250.50, price_now 64341.50 → Δ = +91, leader UP.
        feed = FakePriceSource(Decimal("64341.50"), ts=WS)
        rec = Recorder(store, HttpClobWS("wss://x"), feed, SimClock(WS), mode="readonly")
        rnd = _round()
        await rec.record_round(rnd)
        tick = await rec.record_price_tick(rnd)
        assert tick.price == Decimal("64341.50")

        signals = await store.get_signals(48247)
        assert len(signals) == 1
        sig = signals[0]
        assert sig.price_now == Decimal("64341.50")
        assert sig.delta == Decimal("91.00")
        assert sig.delta != Decimal("0")  # Δ non-nol & masuk akal
        assert sig.leader == "UP"

    async def test_record_price_tick_negative_delta_down(self, store: Store) -> None:
        # start_price 64250.50, price_now 64200.50 → Δ = -50, leader DOWN.
        feed = FakePriceSource(Decimal("64200.50"), ts=WS)
        rec = Recorder(store, HttpClobWS("wss://x"), feed, SimClock(WS))
        rnd = _round()
        await rec.record_round(rnd)
        await rec.record_price_tick(rnd)
        sig = (await store.get_signals(48247))[0]
        assert sig.delta == Decimal("-50.00")
        assert sig.leader == "DOWN"


class TestRecorderGaps:
    async def test_on_circuit_event_and_flush(self, store: Store) -> None:
        feed = FakePriceSource(Decimal("64000"))
        rec = Recorder(store, HttpClobWS("wss://x"), feed, SimClock(WS))
        rec.on_circuit_event(CircuitEvent(EventType.CONNECTED, WS))  # diabaikan
        rec.on_circuit_event(CircuitEvent(EventType.DISCONNECTED, WS, "WSS down"))
        rec.on_circuit_event(CircuitEvent(EventType.STALE, WS, "no data"))
        written = await rec.flush_gaps(48247)
        assert written == 2
        snaps = await store.get_book_snapshots(48247)
        assert all(s.gap for s in snaps)
        assert len(snaps) == 2


class TestRecorderConsumeMarket:
    async def test_consume_writes_snapshots_and_gap(self, store: Store) -> None:
        conn = FakeConnection([("msg", _book_msg("111")), ("msg", _book_msg("111")), ("close",)])
        feed = FakePriceSource(Decimal("64000"))
        clock = SimClock(WS)
        rec = Recorder(store, HttpClobWS("wss://x"), feed, clock, mode="readonly")
        ws = HttpClobWS(
            "wss://x",
            connect=_single_conn_factory(conn),
            clock=clock,
            sleep=_no_sleep,
            event_sink=rec.on_circuit_event,
            max_reconnects=0,
        )
        rec._ws = ws  # inject WS yang sudah berbagi event_sink ke recorder

        count = await rec.consume_market(48247, ["111"], limit=None)
        assert count == 2
        snaps = await store.get_book_snapshots(48247)
        non_gap = [s for s in snaps if not s.gap]
        gaps = [s for s in snaps if s.gap]
        assert len(non_gap) == 2
        # DISCONNECTED + GAVE_UP (max_reconnects=0) → dua penanda gap.
        assert len(gaps) == 2
        assert non_gap[0].best_bid == Decimal("0.52")

    async def test_consume_respects_limit(self, store: Store) -> None:
        conn = FakeConnection([("msg", _book_msg()), ("msg", _book_msg()), ("msg", _book_msg())])
        feed = FakePriceSource(Decimal("64000"))
        clock = SimClock(WS)
        rec = Recorder(store, HttpClobWS("wss://x"), feed, clock)
        rec._ws = HttpClobWS(
            "wss://x",
            connect=_single_conn_factory(conn),
            clock=clock,
            sleep=_no_sleep,
            max_reconnects=0,
        )
        count = await rec.consume_market(48247, ["111"], limit=2)
        assert count == 2
