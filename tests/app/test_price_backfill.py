"""Unit tests for btcbot.app.price_backfill (Bug B9 — historical backfill)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from btcbot.app.price_backfill import backfill_all, backfill_round
from btcbot.data.store import Store
from btcbot.domain.models import Outcome, PriceTick, Round, RoundStatus, Signal

WS = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)
WE = datetime(2026, 6, 26, 13, 20, 0, tzinfo=UTC)


def _round(i: int = 0) -> Round:
    end = WE + timedelta(minutes=5 * i)
    return Round(
        condition_id=f"0xc{i}",
        round_no=int(end.timestamp()),
        token_id_up=f"u{i}",
        token_id_down=f"d{i}",
        window_start=end - timedelta(minutes=5),
        window_end=end,
        start_price=Decimal("65000"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("1"),
        status=RoundStatus.RESOLVED,
        resolved_outcome=Outcome.UP,
    )


class _FakeHistory:
    """PriceHistory palsu: yield deret harga bergerak dalam jendela."""

    def __init__(self, *, count: int = 5, step: str = "20") -> None:
        self._count = count
        self._step = Decimal(step)
        self.calls = 0

    async def iter_window(self, start: datetime, end: datetime) -> AsyncIterator[PriceTick]:
        self.calls += 1
        span = (end - start).total_seconds()
        for i in range(self._count):
            ts = start + timedelta(seconds=span * i / self._count)
            yield PriceTick(
                Decimal("65000") + self._step * i, ts, "fake-history", i + 1, stale=False
            )


@pytest.fixture
async def store() -> AsyncIterator[Store]:
    s = await Store.open("sqlite+aiosqlite:///:memory:")
    try:
        yield s
    finally:
        await s.close()


class TestBackfillRound:
    async def test_writes_samples_for_empty_round(self, store: Store) -> None:
        rnd = _round()
        await store.upsert_round(rnd)
        await store.set_resolution(rnd.round_no, Outcome.UP)
        history = _FakeHistory(count=5)
        n = await backfill_round(store, history, rnd)
        assert n == 5
        signals = await store.get_signals(rnd.round_no)
        assert len(signals) == 5
        assert any(s.delta != Decimal("0") for s in signals)  # harga bergerak

    async def test_idempotent_skips_round_with_samples(self, store: Store) -> None:
        rnd = _round()
        await store.upsert_round(rnd)
        await store.set_resolution(rnd.round_no, Outcome.UP)
        history = _FakeHistory(count=5)
        first = await backfill_round(store, history, rnd)
        assert first == 5
        # panggilan kedua: ronde sudah punya >= min_existing sampel → skip.
        second = await backfill_round(store, history, rnd)
        assert second == 0
        assert len(await store.get_signals(rnd.round_no)) == 5  # tidak dobel

    async def test_skips_round_with_existing_min(self, store: Store) -> None:
        rnd = _round()
        await store.upsert_round(rnd)
        # sudah ada 2 sampel (>= min_existing 2) → skip.
        for i in range(2):
            await store.insert_signal(
                Signal(
                    rnd.round_no,
                    WS + timedelta(seconds=i),
                    Decimal("65010"),
                    Decimal("10"),
                    60.0,
                    Decimal("0"),
                    "UP",
                    Decimal("0"),
                    Decimal("0"),
                ),
                mode="readonly",
            )
        history = _FakeHistory(count=5)
        assert await backfill_round(store, history, rnd) == 0
        assert history.calls == 0  # tak menyentuh history


class TestBackfillAll:
    async def test_backfills_only_empty_rounds(self, store: Store) -> None:
        # 3 ronde resolved kosong + 1 yang sudah punya sampel.
        for i in range(3):
            rnd = _round(i)
            await store.upsert_round(rnd)
            await store.set_resolution(rnd.round_no, Outcome.UP)
        full = _round(9)
        await store.upsert_round(full)
        await store.set_resolution(full.round_no, Outcome.UP)
        for k in range(2):
            await store.insert_signal(
                Signal(
                    full.round_no,
                    WS + timedelta(seconds=k),
                    Decimal("65010"),
                    Decimal("10"),
                    60.0,
                    Decimal("0"),
                    "UP",
                    Decimal("0"),
                    Decimal("0"),
                ),
                mode="readonly",
            )

        rounds_done, samples = await backfill_all(store, _FakeHistory(count=4))
        assert rounds_done == 3  # hanya 3 kosong
        assert samples == 12  # 3 x 4

    async def test_idempotent_second_run(self, store: Store) -> None:
        rnd = _round()
        await store.upsert_round(rnd)
        await store.set_resolution(rnd.round_no, Outcome.UP)
        history = _FakeHistory(count=4)
        first_rounds, _ = await backfill_all(store, history)
        assert first_rounds == 1
        second_rounds, second_samples = await backfill_all(store, history)
        assert second_rounds == 0  # idempoten
        assert second_samples == 0
