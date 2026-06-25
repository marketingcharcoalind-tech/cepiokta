"""Domain entities (docs/07-DATA_MODEL.md).

Semua entitas bersifat murni (tanpa I/O). Aturan numerik (docs/03 §3.5):
- Uang/harga/ukuran memakai :class:`decimal.Decimal` (JANGAN ``float``).
- Semua waktu = ``datetime`` tz-aware UTC.

Dataclass dibuat ``frozen=True`` agar immutable (aman dibagikan & di-hash).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable


class RoundStatus(StrEnum):
    """Status siklus hidup satu ronde market."""

    SCHEDULED = "scheduled"
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"


class Outcome(StrEnum):
    """Hasil resolusi market."""

    UP = "UP"
    DOWN = "DOWN"


class MarketStatus(StrEnum):
    """Status discovery market (Gamma)."""

    OPEN = "open"  # aktif / dapat ditradingkan / akan datang
    CLOSED = "closed"  # window tutup, belum/ tanpa resolusi terbaca
    RESOLVED = "resolved"  # sudah ada outcome pemenang


@dataclass(frozen=True, slots=True)
class Round:
    """Satu ronde market BTC Up/Down (mis. window 5 menit)."""

    condition_id: str
    round_no: int
    token_id_up: str
    token_id_down: str
    window_start: datetime
    window_end: datetime
    start_price: Decimal
    tick_size: Decimal
    min_order_size: Decimal
    status: RoundStatus
    resolved_outcome: Outcome | None = None


@dataclass(frozen=True, slots=True)
class BookLevel:
    """Satu level harga pada orderbook."""

    price: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class OrderBook:
    """Snapshot orderbook untuk satu token outcome."""

    token_id: str
    ts: datetime
    bids: list[BookLevel]
    asks: list[BookLevel]


@dataclass(frozen=True, slots=True)
class Signal:
    """Hasil perhitungan sinyal/edge pada satu tick (docs/05)."""

    round_no: int
    ts: datetime
    price_now: Decimal
    delta: Decimal
    time_left_sec: float
    p_win: Decimal
    leader: str
    ask_win: Decimal
    net_edge: Decimal


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """Permintaan order sebelum dikirim ke OMS."""

    client_id: str
    token_id: str
    side: str  # BUY | SELL
    price: Decimal
    size: Decimal
    order_type: str  # FOK | FAK | GTC


@dataclass(frozen=True, slots=True)
class OrderAck:
    """Acknowledgement order dari venue/paper-OMS."""

    client_id: str
    order_id: str
    status: str
    ts: datetime


@dataclass(frozen=True, slots=True)
class Fill:
    """Eksekusi (sebagian/penuh) sebuah order."""

    order_id: str
    token_id: str
    price: Decimal
    size: Decimal
    ts: datetime


@dataclass(frozen=True, slots=True)
class Position:
    """Posisi terbuka pada satu token outcome."""

    round_no: int
    token_id: str
    size: Decimal
    avg_price: Decimal


@dataclass(frozen=True, slots=True)
class RoundResult:
    """Ringkasan PnL satu ronde setelah settle."""

    round_no: int
    side_taken: str
    entry_price: Decimal
    size: Decimal
    hedge_cost: Decimal
    settled: Decimal
    pnl: Decimal
    balance_after: Decimal


@dataclass(frozen=True, slots=True)
class PriceTick:
    """Satu pembacaan harga dari sumber kebenaran (mis. Chainlink BTC/USD).

    Attributes:
        price: Harga USD ter-normalisasi (Decimal, sudah diskala desimal feed).
        ts: Waktu update harga (UTC aware) — ``updatedAt`` dari feed.
        source: Label sumber (mis. ``"chainlink:data_feed"``).
        round_id: ID ronde feed (``roundId`` aggregator).
        stale: True bila harga dianggap basi (umur > ambang staleness).
    """

    price: Decimal
    ts: datetime
    source: str
    round_id: int
    stale: bool


@runtime_checkable
class PriceSource(Protocol):
    """Kontrak sumber harga BTC/USD (murni & injectable).

    Implementasi: :class:`~btcbot.adapters.chainlink.ChainlinkDataFeed`
    (Data Feeds) dan dapat ditambah adapter Data Streams kemudian tanpa
    mengubah pemanggil.
    """

    async def price_now(self) -> PriceTick:
        """Kembalikan harga BTC/USD terkini sebagai :class:`PriceTick`."""
        ...


@dataclass(frozen=True, slots=True)
class RoundMeta:
    """Metadata ronde hasil discovery Gamma (sebelum diperkaya start_price).

    Berbeda dari :class:`Round`: tidak memuat ``round_no`` maupun
    ``start_price`` (keduanya ditambahkan di lapisan app/Fase 1 — round_no
    diturunkan dari jadwal, start_price dari Chainlink saat window dibuka).
    """

    market_id: str
    condition_id: str
    slug: str
    token_id_up: str
    token_id_down: str
    start_time: datetime
    end_time: datetime
    tick_size: Decimal
    min_order_size: Decimal
    status: MarketStatus
    outcome: Outcome | None = None


def round_from_meta(meta: RoundMeta, *, round_no: int, start_price: Decimal) -> Round:
    """Perkaya :class:`RoundMeta` menjadi :class:`Round` (app/Fase 1+).

    Args:
        meta: Metadata discovery.
        round_no: Nomor ronde (diturunkan dari jadwal, mis. epoch window).
        start_price: Harga acuan BTC/USD saat window dibuka (dari Chainlink).
    """
    status_map = {
        MarketStatus.OPEN: RoundStatus.ACTIVE,
        MarketStatus.CLOSED: RoundStatus.CLOSED,
        MarketStatus.RESOLVED: RoundStatus.RESOLVED,
    }
    return Round(
        condition_id=meta.condition_id,
        round_no=round_no,
        token_id_up=meta.token_id_up,
        token_id_down=meta.token_id_down,
        window_start=meta.start_time,
        window_end=meta.end_time,
        start_price=start_price,
        tick_size=meta.tick_size,
        min_order_size=meta.min_order_size,
        status=status_map[meta.status],
        resolved_outcome=meta.outcome,
    )
