"""Unit tests for btcbot.adapters.gamma (real-schema parse, filter, fixture)."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from btcbot.adapters.clock import SimClock
from btcbot.adapters.gamma import (
    GammaError,
    GammaSchemaError,
    HttpGammaClient,
    is_btc5m_market,
    parse_market,
)
from btcbot.domain.models import MarketStatus, Outcome, RoundMeta

BASE_URL = "https://gamma.example.test"
FIXTURE = Path(__file__).parent.parent / "fixtures" / "gamma_btc5m_markets.json"


def _markets() -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", json.loads(FIXTURE.read_text(encoding="utf-8")))


def _btc5m_open() -> dict[str, Any]:
    return _markets()[0]


def _btc5m_resolved() -> dict[str, Any]:
    return _markets()[1]


def _eth_1h() -> dict[str, Any]:
    return _markets()[2]


def _btc_yesno() -> dict[str, Any]:
    return _markets()[3]


class TestParseMarket:
    def test_maps_all_domain_fields(self) -> None:
        meta = parse_market(_btc5m_open())
        assert isinstance(meta, RoundMeta)
        assert meta.market_id == "512345"
        assert meta.condition_id.startswith("0xaaa")
        assert meta.slug == "bitcoin-up-or-down-june-25-7pm-et"
        assert meta.start_time == datetime(2026, 6, 25, 19, 0, tzinfo=UTC)
        assert meta.end_time == datetime(2026, 6, 25, 19, 5, tzinfo=UTC)
        assert meta.tick_size == Decimal("0.01")
        assert meta.min_order_size == Decimal("5")
        assert meta.status is MarketStatus.OPEN
        assert meta.outcome is None

    def test_token_ids_not_swapped(self) -> None:
        meta = parse_market(_btc5m_open())
        # outcomes=[Up,Down] → token[0]=UP, token[1]=DOWN
        assert meta.token_id_up.startswith("71321045679")
        assert meta.token_id_down.startswith("52114319501")

    def test_times_parsed_utc(self) -> None:
        meta = parse_market(_btc5m_open())
        assert meta.start_time.tzinfo is not None
        assert meta.start_time.utcoffset() is not None

    def test_resolved_status_and_outcome(self) -> None:
        meta = parse_market(_btc5m_resolved())
        assert meta.status is MarketStatus.RESOLVED
        assert meta.outcome is Outcome.UP  # outcomePrices ["1","0"] → Up menang

    def test_decimal_types(self) -> None:
        meta = parse_market(_btc5m_open())
        assert isinstance(meta.tick_size, Decimal)
        assert isinstance(meta.min_order_size, Decimal)

    def test_missing_required_field_raises_clear_error(self) -> None:
        data = _btc5m_open()
        del data["conditionId"]
        with pytest.raises(GammaSchemaError, match="conditionId"):
            parse_market(data)

    def test_missing_token_ids_raises(self) -> None:
        data = _btc5m_open()
        del data["clobTokenIds"]
        with pytest.raises(GammaSchemaError, match="clobTokenIds"):
            parse_market(data)

    def test_non_up_down_outcomes_rejected(self) -> None:
        with pytest.raises(GammaSchemaError, match="Up/Down"):
            parse_market(_btc_yesno())

    def test_bad_json_array_raises(self) -> None:
        data = _btc5m_open()
        data["outcomes"] = "not-json"
        with pytest.raises(GammaSchemaError, match="outcomes"):
            parse_market(data)

    def test_non_iso_date_raises(self) -> None:
        data = _btc5m_open()
        data["endDate"] = "25/06/2026"
        with pytest.raises(GammaSchemaError, match="endDate"):
            parse_market(data)


class TestFilter:
    def test_btc5m_passes(self) -> None:
        assert is_btc5m_market(_btc5m_open()) is True
        assert is_btc5m_market(_btc5m_resolved()) is True

    def test_eth_1h_rejected(self) -> None:
        # outcomes Up/Down tapi durasi 1 jam → ditolak (bukan 5m).
        assert is_btc5m_market(_eth_1h()) is False

    def test_yesno_rejected(self) -> None:
        assert is_btc5m_market(_btc_yesno()) is False

    def test_non_btc_rejected_even_if_5m(self) -> None:
        data = _btc5m_open()
        data["slug"] = "ethereum-up-or-down-x"
        data["question"] = "Ethereum Up or Down?"
        assert is_btc5m_market(data) is False

    def test_missing_dates_returns_false_not_crash(self) -> None:
        data = _btc5m_open()
        del data["startDate"]
        assert is_btc5m_market(data) is False


class TestHttpGammaClient:
    @respx.mock
    async def test_discover_rounds_filters_btc5m_only(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        async with HttpGammaClient(BASE_URL, page_limit=100) as client:
            rounds = await client.discover_rounds()
        # Hanya 2 market BTC 5m (open + resolved) yang lolos.
        assert len(rounds) == 2
        assert {r.market_id for r in rounds} == {"512345", "512300"}

    @respx.mock
    async def test_discover_active_round_picks_window_now(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        # now di dalam window 19:00-19:05 → pilih market 512345.
        clock = SimClock(datetime(2026, 6, 25, 19, 2, tzinfo=UTC))
        async with HttpGammaClient(BASE_URL, clock=clock) as client:
            active = await client.discover_active_round()
        assert active.market_id == "512345"
        assert active.status is MarketStatus.OPEN

    @respx.mock
    async def test_discover_active_round_picks_upcoming(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        # now sebelum semua window → pilih yang terdekat akan datang (18:55).
        clock = SimClock(datetime(2026, 6, 25, 18, 0, tzinfo=UTC))
        async with HttpGammaClient(BASE_URL, clock=clock) as client:
            active = await client.discover_active_round()
        assert active.market_id == "512300"

    @respx.mock
    async def test_no_rounds_raises(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=[_btc_yesno()]))
        async with HttpGammaClient(BASE_URL) as client:
            with pytest.raises(GammaError, match="tidak ada"):
                await client.discover_active_round()

    @respx.mock
    async def test_pagination_accumulates(self) -> None:
        page0 = [_btc5m_open()] * 100  # penuh → minta halaman berikutnya
        page1 = [_btc5m_resolved()]

        def handler(request: httpx.Request) -> httpx.Response:
            offset = request.url.params.get("offset")
            return httpx.Response(200, json=page0 if offset == "0" else page1)

        respx.get(f"{BASE_URL}/markets").mock(side_effect=handler)
        async with HttpGammaClient(BASE_URL, page_limit=100) as client:
            rounds = await client.discover_rounds()
        assert len(rounds) == 101

    @respx.mock
    async def test_rate_limit_backoff_then_success(self) -> None:
        calls = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=[_btc5m_open()])

        respx.get(f"{BASE_URL}/markets").mock(side_effect=handler)

        async def _no_sleep(_s: float) -> None:
            return None

        async with HttpGammaClient(BASE_URL, sleep=_no_sleep, max_retries=3) as client:
            rounds = await client.discover_rounds()
        assert calls["n"] == 2  # 429 lalu sukses
        assert len(rounds) == 1

    @respx.mock
    async def test_get_market_by_condition_id(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(
            return_value=httpx.Response(200, json=[_btc5m_resolved()])
        )
        async with HttpGammaClient(BASE_URL) as client:
            meta = await client.get_market("0xbbb")
        assert meta.market_id == "512300"
        assert meta.outcome is Outcome.UP
