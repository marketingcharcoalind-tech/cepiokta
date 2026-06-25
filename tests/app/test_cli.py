"""Integration tests for btcbot.app.cli (readonly happy-path, fake adapters)."""

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from btcbot.adapters.chainlink import FakePriceSource, PriceUnavailableError
from btcbot.adapters.clob_ws import HttpClobWS, WSConnection, WSConnectionClosedError
from btcbot.adapters.clock import SimClock
from btcbot.app.cli import run_boot_sequence, run_readonly
from btcbot.config.settings import Mode, Settings
from btcbot.data.recorder import Recorder
from btcbot.data.store import Store
from btcbot.domain.models import MarketStatus, Outcome, RoundMeta, RoundStatus

WS_T = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
WE_T = datetime(2026, 6, 25, 10, 5, tzinfo=UTC)
ROUND_NO = int(WS_T.timestamp())  # round_no diturunkan dari epoch window start


def _meta(market_no: int) -> RoundMeta:
    return RoundMeta(
        market_id=str(market_no),
        condition_id=f"0x{market_no}",
        slug=f"bitcoin-up-or-down-{market_no}",
        token_id_up=f"up-{market_no}",
        token_id_down=f"down-{market_no}",
        start_time=WS_T,
        end_time=WE_T,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        status=MarketStatus.OPEN,
    )


class FakeGamma:
    def __init__(self, metas: list[RoundMeta]) -> None:
        self._metas = metas
        self._idx = 0
        self._by_cond = {m.condition_id: m for m in metas}

    async def discover_active_round(self) -> RoundMeta:
        meta = self._metas[self._idx]
        self._idx += 1
        return meta

    async def discover_rounds(self) -> list[RoundMeta]:
        return list(self._metas)

    async def get_market(self, condition_id: str) -> RoundMeta:
        meta = self._by_cond[condition_id]
        return dataclasses.replace(meta, status=MarketStatus.RESOLVED, outcome=Outcome.UP)


def _book_msg(token_id: str) -> str:
    return json.dumps(
        {
            "event_type": "book",
            "asset_id": token_id,
            "timestamp": "2026-06-25T10:00:00Z",
            "bids": [{"price": "0.52", "size": "100"}],
            "asks": [{"price": "0.55", "size": "80"}],
        }
    )


class _Conn:
    def __init__(self) -> None:
        self._script = [_book_msg("up"), _book_msg("down")]
        self._idx = 0

    async def send(self, message: str) -> None:
        return None

    async def recv(self) -> str:
        if self._idx >= len(self._script):
            raise WSConnectionClosedError("selesai")
        msg = self._script[self._idx]
        self._idx += 1
        return msg

    async def close(self) -> None:
        return None


def _fresh_ws_factory() -> Callable[[str], Awaitable[WSConnection]]:
    async def factory(_url: str) -> WSConnection:
        return _Conn()

    return factory


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def store() -> AsyncIterator[Store]:
    s = await Store.open("sqlite+aiosqlite:///:memory:")
    try:
        yield s
    finally:
        await s.close()


def _settings() -> Settings:
    return Settings(mode=Mode.READONLY, paper_starting_balance=Decimal("200"))


class TestBootSequence:
    def test_prints_reference_style(self) -> None:
        lines: list[str] = []
        run_boot_sequence(out=lines.append, version="0.1.0")
        assert lines[0] == "5min-btc-polymarket v0.1.0"
        assert lines[-1] == "all systems go."
        joined = "\n".join(lines)
        for label in (
            "connecting to Polymarket",
            "authenticating wallet",
            "opening websocket feed (BTC 5m)",
            "loading interval-loader module",
            "loading trend module",
            "loading hedging module",
        ):
            assert label in joined
        assert joined.count("[ ok ]") == 6


class TestRunReadonly:
    async def test_records_round_book_and_resolution(self, store: Store) -> None:
        gamma = FakeGamma([_meta(48247)])
        clock = SimClock(WS_T)
        ws = HttpClobWS(
            "wss://x",
            connect=_fresh_ws_factory(),
            clock=clock,
            sleep=_no_sleep,
            max_reconnects=0,
        )
        feed = FakePriceSource(Decimal("64091"), ts=WS_T)
        recorder = Recorder(store, ws, feed, clock, mode="readonly")
        ws.set_event_sink(recorder.on_circuit_event)

        processed = await run_readonly(
            settings=_settings(),
            gamma=gamma,
            recorder=recorder,
            max_rounds=1,
            updates_per_round=None,
        )
        assert processed == 1

        # Round metadata + resolusi tersimpan (round_no diturunkan dari epoch).
        got = await store.get_round(ROUND_NO)
        assert got is not None
        assert got.status is RoundStatus.RESOLVED
        assert got.resolved_outcome is Outcome.UP
        assert got.start_price == Decimal("64091")  # start_price dari Chainlink tick

        # Orderbook snapshots tersimpan (2 update) + gap dari disconnect.
        snaps = await store.get_book_snapshots(ROUND_NO)
        non_gap = [s for s in snaps if not s.gap]
        assert len(non_gap) == 2
        assert any(s.gap for s in snaps)

    async def test_no_orders_table_written(self, store: Store) -> None:
        # Readonly: tidak ada order yang ditulis.
        gamma = FakeGamma([_meta(48248)])
        clock = SimClock(WS_T)
        ws = HttpClobWS(
            "wss://x", connect=_fresh_ws_factory(), clock=clock, sleep=_no_sleep, max_reconnects=0
        )
        recorder = Recorder(store, ws, FakePriceSource(Decimal("64000")), clock, mode="readonly")
        await run_readonly(settings=_settings(), gamma=gamma, recorder=recorder, max_rounds=1)
        assert await store.get_order("anything") is None

    async def test_stops_on_shutdown_event(self, store: Store) -> None:
        gamma = FakeGamma([_meta(1), _meta(2)])
        clock = SimClock(WS_T)
        ws = HttpClobWS(
            "wss://x", connect=_fresh_ws_factory(), clock=clock, sleep=_no_sleep, max_reconnects=0
        )
        recorder = Recorder(store, ws, FakePriceSource(Decimal("64000")), clock, mode="readonly")
        shutdown = asyncio.Event()
        shutdown.set()  # langsung minta berhenti
        processed = await run_readonly(
            settings=_settings(),
            gamma=gamma,
            recorder=recorder,
            max_rounds=10,
            shutdown=shutdown,
        )
        assert processed == 0

    async def test_price_unavailable_skips_round_no_fake_data(self, store: Store) -> None:
        gamma = FakeGamma([_meta(99)])
        clock = SimClock(WS_T)
        ws = HttpClobWS(
            "wss://x", connect=_fresh_ws_factory(), clock=clock, sleep=_no_sleep, max_reconnects=0
        )
        # Sumber harga gagal → ronde dilewati (tanpa start_price palsu), loop aman.
        feed = FakePriceSource(Decimal("64000"), error=PriceUnavailableError("RPC down"))
        recorder = Recorder(store, ws, feed, clock, mode="readonly")
        processed = await run_readonly(
            settings=_settings(), gamma=gamma, recorder=recorder, max_rounds=1
        )
        assert processed == 1
        # Ronde TIDAK direkam (tak ada nilai start_price palsu).
        assert await store.get_round(ROUND_NO) is None
