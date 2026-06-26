"""Unit tests for btcbot.adapters.gamma (slug-based, live-schema fixture)."""

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
    is_updown_market,
    match_updown_slug,
    parse_market,
)
from btcbot.domain.models import MarketStatus, RoundMeta

BASE_URL = "https://gamma.example.test"
FIXTURE = Path(__file__).parent.parent / "fixtures" / "gamma_updown_live_fixture.json"


def _markets() -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", json.loads(FIXTURE.read_text(encoding="utf-8")))


def _btc5m() -> dict[str, Any]:
    return _markets()[0]


def _btc5m_next() -> dict[str, Any]:
    return _markets()[1]


def _eth15m() -> dict[str, Any]:
    return _markets()[2]


def _yesno() -> dict[str, Any]:
    return _markets()[3]


class TestSlugRegex:
    def test_btc_5m_matches(self) -> None:
        assert match_updown_slug("btc-updown-5m-1782472800") == ("btc", "5m", 1782472800)

    def test_eth_15m_matches(self) -> None:
        assert match_updown_slug("eth-updown-15m-1782473400") == ("eth", "15m", 1782473400)

    def test_yesno_rejected(self) -> None:
        assert match_updown_slug("will-bitcoin-reach-200k-2026") is None

    def test_epoch_not_multiple_rejected(self) -> None:
        # 100 bukan kelipatan 300 → sanity gagal.
        assert match_updown_slug("btc-updown-5m-100") is None

    def test_15m_non_multiple_rejected(self) -> None:
        assert match_updown_slug("btc-updown-15m-1782472800") is None  # bukan kelipatan 900


class TestIsUpdownMarket:
    def test_btc5m_matches_btc5m(self) -> None:
        assert is_updown_market(_btc5m(), "btc", "5m") is True

    def test_btc5m_rejected_for_eth(self) -> None:
        assert is_updown_market(_btc5m(), "eth", "5m") is False

    def test_btc5m_rejected_for_15m(self) -> None:
        assert is_updown_market(_btc5m(), "btc", "15m") is False

    def test_eth15m_matches(self) -> None:
        assert is_updown_market(_eth15m(), "eth", "15m") is True

    def test_yesno_rejected(self) -> None:
        assert is_updown_market(_yesno(), "btc", "5m") is False


class TestParseMarket:
    def test_core_fields(self) -> None:
        meta = parse_market(_btc5m())
        assert isinstance(meta, RoundMeta)
        assert meta.market_id == "901001"
        assert meta.condition_id.startswith("0xbtc5m")
        assert meta.slug == "btc-updown-5m-1782472800"
        assert meta.asset == "btc"
        assert meta.timeframe == "5m"
        assert meta.tick_size == Decimal("0.01")
        assert meta.min_order_size == Decimal("5")
        assert meta.status is MarketStatus.OPEN
        assert meta.outcome is None

    def test_window_from_event_start_not_listing_date(self) -> None:
        meta = parse_market(_btc5m())
        # window_start dari eventStartTime (26 Jun 11:55), BUKAN startDate (25 Jun).
        assert meta.start_time == datetime(2026, 6, 26, 11, 55, tzinfo=UTC)
        assert meta.end_time == datetime(2026, 6, 26, 12, 0, tzinfo=UTC)
        # durasi window benar = 5 menit
        assert (meta.end_time - meta.start_time).total_seconds() == 300
        # startDate (listing) TIDAK dipakai
        assert meta.start_time != datetime(2026, 6, 25, 12, 0, tzinfo=UTC)

    def test_token_up_down_not_swapped(self) -> None:
        meta = parse_market(_btc5m())
        # outcomes[0]=Up → clobTokenIds[0]; outcomes[1]=Down → clobTokenIds[1]
        assert meta.token_id_up.startswith("11111")
        assert meta.token_id_down.startswith("22222")

    def test_resolution_source(self) -> None:
        meta = parse_market(_btc5m())
        assert meta.resolution_source == "https://data.chain.link/streams/btc-usd"

    def test_fee_parsed(self) -> None:
        meta = parse_market(_btc5m())
        assert meta.fees_enabled is True
        assert meta.fee_type == "crypto_fees_v2"
        assert meta.fee_schedule is not None
        assert meta.fee_schedule.exponent == 2
        assert meta.fee_schedule.rate == Decimal("0.0006")
        assert meta.fee_schedule.taker_only is True
        assert meta.fee_schedule.rebate_rate == Decimal("0.0001")

    def test_closed_status(self) -> None:
        data = _btc5m()
        data["closed"] = True
        assert parse_market(data).status is MarketStatus.CLOSED

    def test_non_updown_slug_raises(self) -> None:
        with pytest.raises(GammaSchemaError, match="up/down"):
            parse_market(_yesno())

    def test_missing_required_field_raises(self) -> None:
        data = _btc5m()
        del data["conditionId"]
        with pytest.raises(GammaSchemaError, match="conditionId"):
            parse_market(data)

    def test_missing_min_order_size_raises(self) -> None:
        data = _btc5m()
        del data["orderMinSize"]
        with pytest.raises(GammaSchemaError, match="orderMinSize"):
            parse_market(data)

    def test_fee_schedule_missing_subfield_raises(self) -> None:
        data = _btc5m()
        del data["feeSchedule"]["rate"]
        with pytest.raises(GammaSchemaError, match="rate"):
            parse_market(data)

    def test_window_start_fallback_when_no_event_start(self) -> None:
        data = _btc5m()
        del data["eventStartTime"]
        del data["endDate"]  # paksa fallback epoch utk end juga
        meta = parse_market(data)
        # epoch=1782472800 → end; start = epoch - 300
        assert meta.end_time == datetime.fromtimestamp(1782472800, tz=UTC)
        assert meta.start_time == datetime.fromtimestamp(1782472800 - 300, tz=UTC)


async def _no_sleep(_s: float) -> None:
    return None


class TestHttpGammaClient:
    @respx.mock
    async def test_discover_rounds_btc5m_only(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        async with HttpGammaClient(BASE_URL, asset="btc", timeframe="5m") as client:
            rounds = await client.discover_rounds()
        assert {r.market_id for r in rounds} == {"901001", "901002"}
        assert all(r.asset == "btc" and r.timeframe == "5m" for r in rounds)

    @respx.mock
    async def test_discover_rounds_eth15m(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        async with HttpGammaClient(BASE_URL, asset="eth", timeframe="15m") as client:
            rounds = await client.discover_rounds()
        assert [r.market_id for r in rounds] == ["902001"]

    @respx.mock
    async def test_query_uses_end_date_window(self) -> None:
        route = respx.get(f"{BASE_URL}/markets").mock(
            return_value=httpx.Response(200, json=_markets())
        )
        clock = SimClock(datetime(2026, 6, 26, 11, 57, tzinfo=UTC))
        async with HttpGammaClient(BASE_URL, clock=clock, timeframe="5m") as client:
            await client.discover_rounds()
        params = route.calls[0].request.url.params
        assert params.get("end_date_min") == "2026-06-26T11:57:00Z"
        assert params.get("end_date_max") == "2026-06-26T12:09:00Z"  # +12 menit
        assert params.get("closed") == "false"

    @respx.mock
    async def test_active_round_in_window(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        # 11:57 ∈ [11:55, 12:00) → market 901001
        clock = SimClock(datetime(2026, 6, 26, 11, 57, tzinfo=UTC))
        async with HttpGammaClient(BASE_URL, clock=clock, timeframe="5m") as client:
            active = await client.discover_active_round()
        assert active.market_id == "901001"

    @respx.mock
    async def test_active_round_next_when_between(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        # 11:50 sebelum semua window → pilih terdekat akan datang (901001 @ 11:55)
        clock = SimClock(datetime(2026, 6, 26, 11, 50, tzinfo=UTC))
        async with HttpGammaClient(BASE_URL, clock=clock, timeframe="5m") as client:
            active = await client.discover_active_round()
        assert active.market_id == "901001"

    @respx.mock
    async def test_no_rounds_raises(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=[_yesno()]))
        async with HttpGammaClient(BASE_URL, timeframe="5m") as client:
            with pytest.raises(GammaError, match="tidak ada ronde"):
                await client.discover_active_round()

    @respx.mock
    async def test_rate_limit_backoff_then_success(self) -> None:
        calls = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=_markets())

        respx.get(f"{BASE_URL}/markets").mock(side_effect=handler)
        async with HttpGammaClient(BASE_URL, sleep=_no_sleep, timeframe="5m") as client:
            rounds = await client.discover_rounds()
        assert calls["n"] == 2
        assert len(rounds) == 2

    @respx.mock
    async def test_get_market_by_condition_id(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=[_btc5m()]))
        async with HttpGammaClient(BASE_URL) as client:
            meta = await client.get_market(_btc5m()["conditionId"])
        assert meta.market_id == "901001"

    async def test_invalid_timeframe_rejected(self) -> None:
        with pytest.raises(ValueError, match="timeframe"):
            HttpGammaClient(BASE_URL, timeframe="1h")
