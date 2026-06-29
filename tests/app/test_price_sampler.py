"""Unit tests for btcbot.app.price_sampler (Bug B9 — price trajectory)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from btcbot.adapters.clock import SimClock
from btcbot.app.price_sampler import PriceSampler
from btcbot.data.store import Store
from btcbot.domain.models import PriceTick, Round, RoundStatus

WS = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)
WE = datetime(2026, 6, 26, 13, 15, 10, tzinfo=UTC)  # window pendek 10s untuk test


def _round() -> Round:
    return Round(
        condition_id="0xc",
        round_no=int(WE.timestamp()),
        token_id_up="u",
        token_id_down="d",
        window_start=WS,
        window_end=WE,
        start_price=Decimal("65000"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("1"),
        status=RoundStatus.ACTIVE,
    )


class _MovingPrice:
    """PriceSource palsu: harga naik tiap panggilan (simulasi gerak); bisa gagal."""

    def __init__(self, clock: SimClock, *, step: str = "10", fail_first: int = 0) -> None:
        self._clock = clock
        self._price = Decimal("65000")
        self._step = Decimal(step)
        self._fail_first = fail_first
        self.calls = 0

    async def price_now(self) -> PriceTick:
        self.calls += 1
        if self.calls <= self._fail_first:
            raise RuntimeError("rpc down")
        tick = PriceTick(self._price, self._clock.now(), "fake", self.calls, stale=False)
        self._price += self._step
        return tick


class _FlatPrice:
    """PriceSource palsu: harga TETAP (untuk uji dedup + force cadence)."""

    def __init__(self, clock: SimClock) -> None:
        self._clock = clock
        self.calls = 0

    async def price_now(self) -> PriceTick:
        self.calls += 1
        return PriceTick(Decimal("65000"), self._clock.now(), "fake", 1, stale=False)


class _AdvancingSleep:
    """Sleep palsu: catat durasi + majukan SimClock (agar deadline tercapai)."""

    def __init__(self, clock: SimClock) -> None:
        self._clock = clock
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self._clock.advance(timedelta(seconds=delay))


@pytest.fixture
async def store() -> AsyncIterator[Store]:
    s = await Store.open("sqlite+aiosqlite:///:memory:")
    try:
        yield s
    finally:
        await s.close()


def _sampler(store: Store, price: object, clock: SimClock, **kw: object) -> PriceSampler:
    base: dict[str, object] = {
        "mode": "readonly",
        "sample_seconds": 2.0,
        "tail_seconds": 0.5,
        "tail_window_seconds": 4,
        "drain_seconds": 2,
        "force_seconds": 25.0,
    }
    base.update(kw)
    return PriceSampler(store, price, clock, sleep=_AdvancingSleep(clock), **base)  # type: ignore[arg-type]


class TestPriceSampler:
    async def test_writes_multiple_samples_and_terminates(self, store: Store) -> None:
        clock = SimClock(WS)
        rnd = _round()
        sampler = PriceSampler(
            store,
            _MovingPrice(clock),
            clock,
            sample_seconds=2.0,
            tail_seconds=0.5,
            tail_window_seconds=4,
            drain_seconds=2,
            sleep=_AdvancingSleep(clock),
        )
        written = await sampler.run(rnd)  # harus return (tidak menggantung)
        assert written > 1
        signals = await store.get_signals(rnd.round_no)
        assert len(signals) == written
        # harga bergerak → delta tidak nol di sebagian besar sampel.
        assert any(s.delta != Decimal("0") for s in signals)

    async def test_tail_cadence_faster_than_normal(self, store: Store) -> None:
        clock = SimClock(WS)
        sleep = _AdvancingSleep(clock)
        sampler = PriceSampler(
            store,
            _MovingPrice(clock),
            clock,
            sample_seconds=2.0,
            tail_seconds=0.5,
            tail_window_seconds=4,
            drain_seconds=2,
            sleep=sleep,
        )
        await sampler.run(_round())
        # awal window pakai cadence 2.0; ekor pakai 0.5 (lebih rapat).
        assert sleep.delays[0] == 2.0
        assert 0.5 in sleep.delays
        # 0.5 muncul SETELAH 2.0 (ekor di akhir).
        assert sleep.delays.index(0.5) > sleep.delays.index(2.0)

    async def test_tolerates_price_unavailable(self, store: Store) -> None:
        clock = SimClock(WS)
        price = _MovingPrice(clock, fail_first=3)  # 3 poll pertama gagal
        sampler = PriceSampler(
            store,
            price,
            clock,
            sample_seconds=2.0,
            tail_seconds=0.5,
            tail_window_seconds=4,
            drain_seconds=2,
            sleep=_AdvancingSleep(clock),
        )
        written = await sampler.run(_round())  # tak crash meski 3 gagal di awal
        assert written >= 1  # tetap menulis setelah RPC pulih
        assert price.calls > 3

    async def test_dedup_flat_price_writes_less(self, store: Store) -> None:
        # Harga tetap → hanya tulis saat berubah / force cadence → < jumlah poll.
        clock = SimClock(WS)
        price = _FlatPrice(clock)
        sampler = PriceSampler(
            store,
            price,
            clock,
            sample_seconds=2.0,
            tail_seconds=0.5,
            tail_window_seconds=0,  # tanpa ekor → murni dedup
            drain_seconds=0,
            force_seconds=25.0,
            sleep=_AdvancingSleep(clock),
        )
        written = await sampler.run(_round())
        # 1 tulis awal; sisanya di-dedup (harga sama, force 25s belum terlewati di window 10s).
        assert written == 1
        assert price.calls > 1  # tetap poll, tapi tak tulis dobel

    async def test_shutdown_stops_cleanly(self, store: Store) -> None:
        clock = SimClock(WS)
        shutdown = asyncio.Event()
        shutdown.set()
        sampler = _sampler(store, _MovingPrice(clock), clock)
        written = await sampler.run(_round(), shutdown=shutdown)
        assert written == 0  # shutdown sudah set → tak menulis
