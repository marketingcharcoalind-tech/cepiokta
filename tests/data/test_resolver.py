"""Unit tests for btcbot.data.resolver (network mocked)."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from btcbot.adapters.chainlink import FakePriceSource
from btcbot.adapters.clock import SimClock
from btcbot.adapters.gamma import HttpGammaClient
from btcbot.data.resolver import Resolver
from btcbot.data.store import Store
from btcbot.domain.models import Outcome, Round, RoundStatus

NOW = datetime(2026, 6, 26, 13, 0, tzinfo=UTC)
BASE_URL = "https://gamma.example.test"
RESOLVED_FIXTURE = Path(__file__).parent.parent / "fixtures" / "gamma_resolved_markets.json"


def _resolved_markets() -> dict[str, dict[str, Any]]:
    return cast("dict[str, dict[str, Any]]", json.loads(RESOLVED_FIXTURE.read_text("utf-8")))


@pytest.fixture
async def store() -> AsyncIterator[Store]:
    s = await Store.open("sqlite+aiosqlite:///:memory:")
    try:
        yield s
    finally:
        await s.close()


def _round(
    round_no: int,
    cond: str,
    *,
    window_end: datetime,
    start_price: str = "64000",
    status: RoundStatus = RoundStatus.ACTIVE,
) -> Round:
    return Round(
        condition_id=cond,
        round_no=round_no,
        token_id_up=f"up{round_no}",
        token_id_down=f"down{round_no}",
        window_start=window_end - timedelta(minutes=5),
        window_end=window_end,
        start_price=Decimal(start_price),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        status=status,
    )


class FakeLookup:
    """ResolutionLookup mock: condition_id → Outcome|None."""

    def __init__(self, mapping: dict[str, Outcome | None]) -> None:
        self._mapping = mapping

    async def get_resolution(self, condition_id: str) -> Outcome | None:
        return self._mapping.get(condition_id)


class TestResolveRound:
    async def test_gamma_resolved_up(self, store: Store) -> None:
        rnd = _round(1, "0xup", window_end=NOW - timedelta(minutes=10))
        await store.upsert_round(rnd)
        resolver = Resolver(store, FakeLookup({"0xup": Outcome.UP}), SimClock(NOW))
        assert await resolver.resolve_round(rnd) is True
        res = await store.get_resolution(1)
        assert res is not None
        assert res.resolved_outcome is Outcome.UP
        assert res.status == "resolved"
        assert res.resolution_source == "gamma"

    async def test_gamma_resolved_down(self, store: Store) -> None:
        rnd = _round(2, "0xdown", window_end=NOW - timedelta(minutes=10))
        await store.upsert_round(rnd)
        resolver = Resolver(store, FakeLookup({"0xdown": Outcome.DOWN}), SimClock(NOW))
        await resolver.resolve_round(rnd)
        res = await store.get_resolution(2)
        assert res is not None
        assert res.resolved_outcome is Outcome.DOWN

    async def test_not_yet_resolved_unchanged(self, store: Store) -> None:
        rnd = _round(3, "0xpending", window_end=NOW - timedelta(minutes=10))
        await store.upsert_round(rnd)
        resolver = Resolver(store, FakeLookup({"0xpending": None}), SimClock(NOW))
        assert await resolver.resolve_round(rnd) is False
        res = await store.get_resolution(3)
        assert res is not None
        assert res.status != "resolved"
        assert res.resolved_outcome is None

    async def test_already_resolved_not_overwritten(self, store: Store) -> None:
        rnd = _round(4, "0xup", window_end=NOW - timedelta(minutes=10), status=RoundStatus.RESOLVED)
        await store.upsert_round(rnd)
        # lookup mengembalikan DOWN, tapi ronde sudah resolved → tidak ditimpa.
        resolver = Resolver(store, FakeLookup({"0xup": Outcome.DOWN}), SimClock(NOW))
        assert await resolver.resolve_round(rnd) is False


class TestCrossCheck:
    async def test_agreement_no_mismatch(self, store: Store) -> None:
        rnd = _round(5, "0xup", window_end=NOW - timedelta(seconds=30), start_price="64000")
        await store.upsert_round(rnd)
        # Chainlink 65000 >= start 64000 → UP, sepakat dengan Gamma UP.
        price = FakePriceSource(Decimal("65000"), ts=NOW)
        resolver = Resolver(
            store, FakeLookup({"0xup": Outcome.UP}), SimClock(NOW), price_source=price
        )
        with capture_logs() as logs:
            await resolver.resolve_round(rnd)
        assert "resolution_mismatch" not in [entry["event"] for entry in logs]
        res = await store.get_resolution(5)
        assert res is not None
        assert res.settlement_price == Decimal("65000")

    async def test_mismatch_logs_warning(self, store: Store) -> None:
        rnd = _round(6, "0xup", window_end=NOW - timedelta(seconds=30), start_price="64000")
        await store.upsert_round(rnd)
        # Chainlink 63000 < start 64000 → DOWN, beda dari Gamma UP → mismatch.
        price = FakePriceSource(Decimal("63000"), ts=NOW)
        resolver = Resolver(
            store, FakeLookup({"0xup": Outcome.UP}), SimClock(NOW), price_source=price
        )
        with capture_logs() as logs:
            await resolver.resolve_round(rnd)
        assert "resolution_mismatch" in [entry["event"] for entry in logs]
        # outcome tetap Gamma (ground truth), settlement_price Chainlink disimpan.
        res = await store.get_resolution(6)
        assert res is not None
        assert res.resolved_outcome is Outcome.UP
        assert res.settlement_price == Decimal("63000")

    async def test_old_round_skips_cross_check(self, store: Store) -> None:
        # window_end jauh lampau (> 120s) → cross-check dilewati (backfill).
        rnd = _round(7, "0xup", window_end=NOW - timedelta(hours=2))
        await store.upsert_round(rnd)
        price = FakePriceSource(Decimal("63000"), ts=NOW)
        resolver = Resolver(
            store, FakeLookup({"0xup": Outcome.UP}), SimClock(NOW), price_source=price
        )
        await resolver.resolve_round(rnd)
        res = await store.get_resolution(7)
        assert res is not None
        assert res.settlement_price is None  # tak ada cross-check


class TestBackfill:
    async def test_backfill_resolves_all_unresolved(self, store: Store) -> None:
        for i in range(3):
            await store.upsert_round(
                _round(100 + i, f"0xc{i}", window_end=NOW - timedelta(hours=1))
            )
        # satu ronde sudah resolved sebelumnya (tak boleh ditimpa)
        await store.upsert_round(
            _round(200, "0xdone", window_end=NOW - timedelta(hours=1), status=RoundStatus.RESOLVED)
        )
        await store.set_resolution(200, Outcome.DOWN, resolution_source="gamma")

        mapping: dict[str, Outcome | None] = {f"0xc{i}": Outcome.UP for i in range(3)}
        mapping["0xdone"] = Outcome.UP  # akan diabaikan (sudah resolved)
        resolver = Resolver(store, FakeLookup(mapping), SimClock(NOW))

        n = await resolver.backfill()
        assert n == 3
        for i in range(3):
            res = await store.get_resolution(100 + i)
            assert res is not None
            assert res.resolved_outcome is Outcome.UP
        # yang sudah resolved tetap DOWN (tidak ditimpa)
        done = await store.get_resolution(200)
        assert done is not None
        assert done.resolved_outcome is Outcome.DOWN

    async def test_backfill_idempotent(self, store: Store) -> None:
        await store.upsert_round(_round(300, "0xc", window_end=NOW - timedelta(hours=1)))
        resolver = Resolver(store, FakeLookup({"0xc": Outcome.UP}), SimClock(NOW))
        assert await resolver.backfill() == 1
        assert await resolver.backfill() == 0  # sudah resolved → tak ada lagi


class TestResolverWithGammaClient:
    """Integrasi: lookup = HttpGammaClient nyata (respx, fixture RESOLVED asli)."""

    @respx.mock
    async def test_resolve_up_via_gamma(self, store: Store) -> None:
        market = _resolved_markets()["btc-updown-5m-1782480000"]  # ["1","0"] → UP
        route = respx.get(f"{BASE_URL}/markets").mock(
            return_value=httpx.Response(200, json=[market])
        )
        rnd = _round(10, market["conditionId"], window_end=NOW - timedelta(hours=1))
        await store.upsert_round(rnd)
        resolver = Resolver(store, HttpGammaClient(BASE_URL), SimClock(NOW))
        assert await resolver.resolve_round(rnd) is True
        res = await store.get_resolution(10)
        assert res is not None
        assert res.resolved_outcome is Outcome.UP
        assert res.resolution_source == "gamma"
        assert route.calls[0].request.url.params.get("closed") == "true"

    @respx.mock
    async def test_resolve_down_via_gamma(self, store: Store) -> None:
        market = _resolved_markets()["btc-updown-5m-1782477300"]  # ["0","1"] → DOWN
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=[market]))
        rnd = _round(11, market["conditionId"], window_end=NOW - timedelta(hours=1))
        await store.upsert_round(rnd)
        resolver = Resolver(store, HttpGammaClient(BASE_URL), SimClock(NOW))
        await resolver.resolve_round(rnd)
        res = await store.get_resolution(11)
        assert res is not None
        assert res.resolved_outcome is Outcome.DOWN
