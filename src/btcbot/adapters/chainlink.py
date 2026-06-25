"""Chainlink price adapter — BTC/USD "price truth" via Data Feeds (docs/04 §4.6).

Membaca harga BTC/USD on-chain (read-only ``eth_call``, gratis, **tanpa order /
tanpa private key**) dari kontrak Chainlink **Data Feeds** (AggregatorV3) di
Polygon, lalu mengembalikan :class:`~btcbot.domain.models.PriceTick`.

Desain berlapis agar adapter **Data Streams** bisa ditambah kemudian tanpa
mengubah pemanggil:
- :class:`~btcbot.domain.models.PriceSource` (Protocol domain) — kontrak.
- :class:`AggregatorReader` (Protocol) — abstraksi pembacaan on-chain mentah
  (``decimals()`` + ``latestRoundData()``), mudah di-mock saat test.
- :class:`Web3AggregatorReader` — implementasi nyata via ``web3`` (eth_call).
- :class:`ChainlinkDataFeed` — logika normalisasi, validasi, & staleness.
- :class:`FakePriceSource` — deterministik untuk test/backtest/demo.

Aturan numerik (docs/03 §3.5): harga = :class:`decimal.Decimal`; waktu UTC aware.
Tidak pernah "mengarang" harga: kegagalan/anomali → :class:`PriceUnavailableError`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from web3 import AsyncWeb3

from btcbot.domain.models import PriceTick

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from btcbot.adapters.clock import Clock

_T = TypeVar("_T")

# Sanity range default untuk BTC/USD (USD). Mencegah harga absurd dari feed rusak.
_DEFAULT_MIN_PRICE = Decimal("1000")
_DEFAULT_MAX_PRICE = Decimal("1000000")

# ABI minimal AggregatorV3Interface (Chainlink Data Feeds).
AGGREGATOR_V3_ABI: tuple[dict[str, object], ...] = (
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
)


class PriceUnavailableError(Exception):
    """Harga tidak tersedia/terpercaya (RPC gagal, answer<=0, di luar sanity)."""


@dataclass(frozen=True, slots=True)
class AggregatorRoundData:
    """Hasil mentah ``latestRoundData()`` (belum diskala)."""

    round_id: int
    answer: int  # raw int256 (belum dibagi 10**decimals)
    started_at: int
    updated_at: int  # epoch detik
    answered_in_round: int


class AggregatorReader(Protocol):
    """Abstraksi pembacaan on-chain mentah dari aggregator (mock-able)."""

    async def decimals(self) -> int:
        """Kembalikan jumlah desimal feed (``decimals()``)."""
        ...

    async def latest_round_data(self) -> AggregatorRoundData:
        """Kembalikan ``latestRoundData()`` mentah."""
        ...


class ChainlinkDataFeed:
    """Sumber harga BTC/USD dari Chainlink Data Feeds (implementasi PriceSource).

    Args:
        reader: Pembaca aggregator (injectable; nyata atau mock).
        clock: Sumber waktu untuk evaluasi staleness.
        source: Label sumber untuk :class:`PriceTick` (mis. alamat feed).
        max_staleness_sec: Ambang umur ``updatedAt`` → ``stale=True``.
        min_price: Batas bawah sanity range (USD).
        max_price: Batas atas sanity range (USD).
        retries: Jumlah percobaan baca on-chain sebelum menyerah.
        timeout_sec: Timeout per percobaan baca.
        sleep: Fungsi tidur antar-retry (injectable untuk test).
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        reader: AggregatorReader,
        clock: Clock,
        source: str = "chainlink:data_feed",
        max_staleness_sec: int = 120,
        min_price: Decimal = _DEFAULT_MIN_PRICE,
        max_price: Decimal = _DEFAULT_MAX_PRICE,
        retries: int = 3,
        timeout_sec: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._reader = reader
        self._clock = clock
        self._source = source
        self._max_staleness_sec = max_staleness_sec
        self._min_price = min_price
        self._max_price = max_price
        self._retries = max(1, retries)
        self._timeout_sec = timeout_sec
        self._sleep = sleep
        self._decimals: int | None = None

    async def price_now(self) -> PriceTick:
        """Baca, normalisasi, & validasi harga BTC/USD terkini.

        Raises:
            PriceUnavailableError: RPC gagal/timeout, ``answer <= 0``, atau
                harga di luar sanity range.
        """
        decimals = await self._get_decimals()
        data = await self._retry(self._reader.latest_round_data, "latestRoundData")

        if data.answer <= 0:
            raise PriceUnavailableError(f"answer tidak valid (<= 0): {data.answer}")

        price = Decimal(data.answer) / (Decimal(10) ** decimals)

        if not (self._min_price < price < self._max_price):
            raise PriceUnavailableError(
                f"harga di luar sanity range ({self._min_price}, {self._max_price}): {price}"
            )

        ts = datetime.fromtimestamp(data.updated_at, tz=UTC)
        age_sec = (self._clock.now() - ts).total_seconds()
        stale = age_sec > self._max_staleness_sec

        return PriceTick(
            price=price,
            ts=ts,
            source=self._source,
            round_id=data.round_id,
            stale=stale,
        )

    async def _get_decimals(self) -> int:
        if self._decimals is None:
            self._decimals = await self._retry(self._reader.decimals, "decimals")
        return self._decimals

    async def _retry(self, factory: Callable[[], Awaitable[_T]], what: str) -> _T:
        """Jalankan ``factory`` dengan timeout + retry; bungkus error RPC."""
        last_exc: Exception | None = None
        backoff = 0.2
        for attempt in range(self._retries):
            try:
                return await asyncio.wait_for(factory(), timeout=self._timeout_sec)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < self._retries - 1:
                    await self._sleep(backoff)
                    backoff *= 2
        msg = f"gagal membaca {what} setelah {self._retries} percobaan"
        raise PriceUnavailableError(msg) from last_exc


class Web3AggregatorReader:
    """Pembaca aggregator nyata via ``web3`` (read-only ``eth_call``).

    Args:
        rpc_url: URL RPC Polygon (Settings.polygon_rpc_url).
        address: Alamat kontrak feed (Settings.chainlink_btcusd_source).

    Raises:
        ValueError: bila ``rpc_url`` atau ``address`` kosong.
    """

    def __init__(self, rpc_url: str, address: str) -> None:
        if not rpc_url:
            raise ValueError("POLYGON_RPC_URL belum dikonfigurasi")
        if not address:
            raise ValueError("CHAINLINK_BTCUSD_SOURCE (address feed) belum dikonfigurasi")
        self._rpc_url = rpc_url
        self._address = address
        self._contract: Any = None  # lazy init

    def _get_contract(self) -> Any:  # noqa: ANN401 - web3 contract bertipe dinamis (Any)
        if self._contract is None:
            w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self._rpc_url))
            checksum = w3.to_checksum_address(self._address)
            self._contract = w3.eth.contract(address=checksum, abi=list(AGGREGATOR_V3_ABI))
        return self._contract

    async def decimals(self) -> int:
        contract = self._get_contract()
        return int(await contract.functions.decimals().call())

    async def latest_round_data(self) -> AggregatorRoundData:
        contract = self._get_contract()
        result = await contract.functions.latestRoundData().call()
        round_id, answer, started_at, updated_at, answered_in_round = result
        return AggregatorRoundData(
            round_id=int(round_id),
            answer=int(answer),
            started_at=int(started_at),
            updated_at=int(updated_at),
            answered_in_round=int(answered_in_round),
        )


class FakePriceSource:
    """PriceSource deterministik untuk test/backtest/demo.

    Args:
        price: Harga yang dikembalikan.
        ts: Timestamp tick (default epoch UTC bila None — gunakan ``set_*``).
        source: Label sumber.
        round_id: ID ronde feed.
        stale: Tandai basi.
        error: Bila di-set, :meth:`price_now` raise error ini (simulasi gagal).
    """

    def __init__(  # noqa: PLR0913
        self,
        price: Decimal,
        *,
        ts: datetime | None = None,
        source: str = "fake",
        round_id: int = 1,
        stale: bool = False,
        error: Exception | None = None,
    ) -> None:
        self._price = price
        self._ts = ts or datetime(1970, 1, 1, tzinfo=UTC)
        self._source = source
        self._round_id = round_id
        self._stale = stale
        self._error = error

    def set_price(self, price: Decimal) -> None:
        """Ubah harga (simulasi pergerakan)."""
        self._price = price

    async def price_now(self) -> PriceTick:
        if self._error is not None:
            raise self._error
        return PriceTick(
            price=self._price,
            ts=self._ts,
            source=self._source,
            round_id=self._round_id,
            stale=self._stale,
        )
