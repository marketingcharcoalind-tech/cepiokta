"""Unit tests for btcbot.adapters.gamma (slug-based, real live fixture)."""

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

# Identitas market dari capture LIVE asli.
BTC5M_SLUG = "btc-updown-5m-1782478200"
BTC5M_ID = "2681754"
BTC5M_COND = "0xcb030c1a2d17ce560271211695fa8c57699157460f55888bc80a844d2f442129"
BTC5M_UP = "95669407792983147286157493205525826761142684902684717845468525863131348838914"
BTC5M_DOWN = "49735374456142653191678109316724009312054213661784251664093232934088380989209"
BTC15M_SLUG = "btc-updown-15m-1782477900"
# Window BTC 5m (eventStartTime → endDate), 26 Jun 2026.
BTC5M_START = datetime(2026, 6, 26, 12, 50, tzinfo=UTC)
BTC5M_END = datetime(2026, 6, 26, 12, 55, tzinfo=UTC)


def _markets() -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", json.loads(FIXTURE.read_text(encoding="utf-8")))


def _by_slug(slug: str) -> dict[str, Any]:
    for m in _markets():
        if m.get("slug") == slug:
            return m
    raise AssertionError(f"slug {slug} tidak ada di fixture")


def _btc5m() -> dict[str, Any]:
    return _by_slug(BTC5M_SLUG)


async def _no_sleep(_s: float) -> None:
    return None


class TestSlugRegex:
    def test_btc_5m_matches(self) -> None:
        assert match_updown_slug("btc-updown-5m-1782478200") == ("btc", "5m", 1782478200)

    def test_eth_15m_matches(self) -> None:
        assert match_updown_slug("eth-updown-15m-1782477900") == ("eth", "15m", 1782477900)

    def test_yesno_rejected(self) -> None:
        assert match_updown_slug("will-bitcoin-reach-200k-2026") is None

    def test_epoch_not_multiple_rejected(self) -> None:
        assert match_updown_slug("btc-updown-5m-100") is None

    def test_15m_non_multiple_rejected(self) -> None:
        assert match_updown_slug("btc-updown-15m-1782478200") is None  # bukan kelipatan 900


class TestIsUpdownMarket:
    def test_btc5m_matches_btc5m(self) -> None:
        assert is_updown_market(_btc5m(), "btc", "5m") is True

    def test_btc5m_rejected_for_eth(self) -> None:
        assert is_updown_market(_btc5m(), "eth", "5m") is False

    def test_btc5m_rejected_for_15m(self) -> None:
        assert is_updown_market(_btc5m(), "btc", "15m") is False

    def test_btc15m_matches(self) -> None:
        assert is_updown_market(_by_slug(BTC15M_SLUG), "btc", "15m") is True

    def test_yesno_rejected(self) -> None:
        assert is_updown_market({"slug": "will-bitcoin-reach-200k-2026"}, "btc", "5m") is False


class TestParseMarket:
    def test_core_fields(self) -> None:
        meta = parse_market(_btc5m())
        assert isinstance(meta, RoundMeta)
        assert meta.market_id == BTC5M_ID
        assert meta.condition_id == BTC5M_COND
        assert meta.slug == BTC5M_SLUG
        assert meta.asset == "btc"
        assert meta.timeframe == "5m"
        assert meta.tick_size == Decimal("0.01")
        assert meta.min_order_size == Decimal("5")
        assert meta.status is MarketStatus.OPEN
        assert meta.outcome is None

    def test_window_from_event_start_not_listing_date(self) -> None:
        meta = parse_market(_btc5m())
        # window dari eventStartTime (26 Jun 12:50) & endDate (12:55), BUKAN startDate (25 Jun).
        assert meta.start_time == BTC5M_START
        assert meta.end_time == BTC5M_END
        assert (meta.end_time - meta.start_time).total_seconds() == 300
        # startDate listing adalah 2026-06-25 → tidak boleh dipakai sebagai window_start
        assert meta.start_time != datetime(2026, 6, 25, 12, 58, 39, tzinfo=UTC)

    def test_token_up_down_not_swapped(self) -> None:
        meta = parse_market(_btc5m())
        assert meta.token_id_up == BTC5M_UP
        assert meta.token_id_down == BTC5M_DOWN

    def test_resolution_source_data_streams(self) -> None:
        meta = parse_market(_btc5m())
        assert meta.resolution_source == "https://data.chain.link/streams/btc-usd"

    def test_fee_parsed(self) -> None:
        meta = parse_market(_btc5m())
        assert meta.fees_enabled is True
        assert meta.fee_type == "crypto_fees_v2"
        assert meta.fee_schedule is not None
        assert meta.fee_schedule.exponent == 1
        assert meta.fee_schedule.rate == Decimal("0.07")
        assert meta.fee_schedule.taker_only is True
        assert meta.fee_schedule.rebate_rate == Decimal("0.2")

    def test_closed_status(self) -> None:
        data = _btc5m()
        data["closed"] = True
        assert parse_market(data).status is MarketStatus.CLOSED

    def test_non_updown_slug_raises(self) -> None:
        data = _btc5m()
        data["slug"] = "will-bitcoin-reach-200k-2026"
        with pytest.raises(GammaSchemaError, match="up/down"):
            parse_market(data)

    def test_missing_condition_id_raises(self) -> None:
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
        data["feeSchedule"] = dict(data["feeSchedule"])
        del data["feeSchedule"]["rate"]
        with pytest.raises(GammaSchemaError, match="rate"):
            parse_market(data)

    def test_window_fallback_when_no_event_start(self) -> None:
        data = _btc5m()
        data.pop("eventStartTime", None)
        data.pop("events", None)
        data.pop("endDate", None)
        meta = parse_market(data)
        assert meta.end_time == datetime.fromtimestamp(1782478200, tz=UTC)
        assert meta.start_time == datetime.fromtimestamp(1782478200 - 300, tz=UTC)


class TestHttpGammaClient:
    @respx.mock
    async def test_discover_rounds_btc5m_only(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        async with HttpGammaClient(BASE_URL, asset="btc", timeframe="5m") as client:
            rounds = await client.discover_rounds()
        assert rounds, "harus ada ronde btc 5m"
        assert all(r.asset == "btc" and r.timeframe == "5m" for r in rounds)
        assert all(r.slug.startswith("btc-updown-5m-") for r in rounds)
        assert BTC5M_SLUG in {r.slug for r in rounds}

    @respx.mock
    async def test_discover_rounds_btc15m(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        async with HttpGammaClient(BASE_URL, asset="btc", timeframe="15m") as client:
            rounds = await client.discover_rounds()
        assert all(r.timeframe == "15m" and r.asset == "btc" for r in rounds)
        assert BTC15M_SLUG in {r.slug for r in rounds}

    @respx.mock
    async def test_discover_rounds_eth15m(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        async with HttpGammaClient(BASE_URL, asset="eth", timeframe="15m") as client:
            rounds = await client.discover_rounds()
        assert all(r.asset == "eth" and r.timeframe == "15m" for r in rounds)

    @respx.mock
    async def test_query_uses_end_date_window(self) -> None:
        route = respx.get(f"{BASE_URL}/markets").mock(
            return_value=httpx.Response(200, json=_markets())
        )
        clock = SimClock(datetime(2026, 6, 26, 12, 52, tzinfo=UTC))
        async with HttpGammaClient(BASE_URL, clock=clock, timeframe="5m") as client:
            await client.discover_rounds()
        params = route.calls[0].request.url.params
        assert params.get("end_date_min") == "2026-06-26T12:52:00Z"
        assert params.get("end_date_max") == "2026-06-26T13:04:00Z"  # +12 menit
        assert params.get("closed") == "false"

    @respx.mock
    async def test_active_round_in_window(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        clock = SimClock(datetime(2026, 6, 26, 12, 52, tzinfo=UTC))  # ∈ [12:50, 12:55)
        async with HttpGammaClient(BASE_URL, clock=clock, timeframe="5m") as client:
            active = await client.discover_active_round()
        assert active.slug == BTC5M_SLUG
        assert active.condition_id == BTC5M_COND

    @respx.mock
    async def test_active_round_next_when_before_all(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=_markets()))
        clock = SimClock(datetime(2026, 6, 26, 12, 30, tzinfo=UTC))  # sebelum semua window
        async with HttpGammaClient(BASE_URL, clock=clock, timeframe="5m") as client:
            active = await client.discover_active_round()
        # terdekat akan datang = window paling awal (12:50)
        assert active.slug == BTC5M_SLUG

    @respx.mock
    async def test_no_rounds_raises(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(
            return_value=httpx.Response(200, json=[{"slug": "will-bitcoin-reach-200k-2026"}])
        )
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
        assert rounds

    @respx.mock
    async def test_get_market_by_condition_id(self) -> None:
        respx.get(f"{BASE_URL}/markets").mock(return_value=httpx.Response(200, json=[_btc5m()]))
        async with HttpGammaClient(BASE_URL) as client:
            meta = await client.get_market(BTC5M_COND)
        assert meta.market_id == BTC5M_ID
        assert meta.slug == BTC5M_SLUG

    async def test_invalid_timeframe_rejected(self) -> None:
        with pytest.raises(ValueError, match="timeframe"):
            HttpGammaClient(BASE_URL, timeframe="1h")
