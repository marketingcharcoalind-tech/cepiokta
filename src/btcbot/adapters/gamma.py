"""Gamma API adapter — market discovery BTC 5m (docs/04 §4.3, docs/08 §8.2).

Membaca market dari **Gamma REST API publik** (read-only, tanpa auth, tanpa
order) lalu memetakan ke :class:`~btcbot.domain.models.RoundMeta`.

Skema respons Gamma (camelCase) yang dipetakan (lihat dokumentasi resmi
Polymarket Gamma Markets API):
- ``id``                    → ``market_id``
- ``conditionId``           → ``condition_id``
- ``slug``                  → ``slug``
- ``startDate`` / ``endDate`` (ISO-8601) → ``start_time`` / ``end_time`` (UTC)
- ``outcomes``              → label outcome (string ber-JSON, mis. ``"[\"Up\",\"Down\"]"``)
- ``clobTokenIds``          → token id per-outcome (string ber-JSON; URUTAN
  SAMA dengan ``outcomes`` → token UP/DOWN dipetakan via label, bukan posisi
  tebakan, sehingga tidak tertukar)
- ``outcomePrices``         → untuk menentukan pemenang saat resolved
- ``orderPriceMinTickSize`` → ``tick_size``
- ``orderMinSize``          → ``min_order_size``
- ``active`` / ``closed``   → status OPEN/CLOSED/RESOLVED

Kriteria filter seri **BTC 5-minute** (struktural, bukan teks judul rapuh):
1. ``outcomes`` (di-parse) == {``up``, ``down``} (case-insensitive).
2. Aset cocok: ``slug``/``question`` memuat salah satu ``asset_keywords``
   (default ``bitcoin``/``btc``).
3. Timeframe: durasi ``end-start`` ≈ ``timeframe_sec`` (default 300s) dalam
   toleransi ``tolerance_sec`` (default 15s). Durasi window adalah sinyal
   paling andal untuk "5 menit".

.. warning::
   Skema di bawah mengikuti **dokumentasi Gamma**; satu kali **capture respons
   live** lalu update fixture ``tests/fixtures/gamma_btc5m_markets.json``
   disarankan untuk mengunci regresi schema (lihat docs/04 §4.8).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from btcbot.adapters.clock import Clock, SystemClock
from btcbot.domain.models import MarketStatus, Outcome, RoundMeta

if TYPE_CHECKING:
    from types import TracebackType

ENDPOINT_MARKETS = "/markets"
DEFAULT_TIMEOUT = 10.0
DEFAULT_PAGE_LIMIT = 100
DEFAULT_MAX_PAGES = 20
_HTTP_SERVER_ERROR = 500

DEFAULT_ASSET_KEYWORDS = ("bitcoin", "btc")
DEFAULT_TIMEFRAME_SEC = 300
DEFAULT_TOLERANCE_SEC = 15

SleepFunc = Callable[[float], Awaitable[None]]


class GammaError(Exception):
    """Kesalahan umum adapter Gamma."""


class GammaSchemaError(GammaError):
    """Respons Gamda tidak sesuai skema (field wajib hilang/tak terbaca)."""


class GammaClient(Protocol):
    """Kontrak discovery market (docs/08 §8.2)."""

    async def discover_active_round(self) -> RoundMeta:
        """Kembalikan ronde BTC 5m yang aktif kini / terdekat akan datang."""
        ...

    async def discover_rounds(self) -> list[RoundMeta]:
        """Kembalikan daftar ronde BTC 5m (untuk penjadwalan scanner)."""
        ...

    async def get_market(self, condition_id: str) -> RoundMeta:
        """Kembalikan satu market berdasarkan ``condition_id``."""
        ...


# ----- parsing helpers -----


def _require(data: dict[str, Any], key: str) -> Any:  # noqa: ANN401 - nilai JSON dinamis
    """Ambil field wajib; raise :class:`GammaSchemaError` bila hilang/None."""
    if key not in data or data[key] is None:
        raise GammaSchemaError(f"field wajib '{key}' hilang dari respons Gamma")
    return data[key]


def _json_list(value: object, key: str) -> list[Any]:
    """Decode field Gamma yang berupa array ber-JSON (string) atau list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise GammaSchemaError(f"field '{key}' bukan JSON valid: {value!r}") from exc
        if not isinstance(parsed, list):
            raise GammaSchemaError(f"field '{key}' bukan list: {value!r}")
        return parsed
    raise GammaSchemaError(f"field '{key}' bertipe tak terduga: {type(value).__name__}")


def _parse_utc(value: str, key: str) -> datetime:
    """Parse ISO-8601 (termasuk sufiks ``Z``) menjadi datetime UTC aware."""
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GammaSchemaError(f"field '{key}' bukan ISO-8601: {value!r}") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise GammaSchemaError(f"field '{key}' harus tz-aware: {value!r}")
    return dt.astimezone(UTC)


def _to_decimal(value: object, key: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise GammaSchemaError(f"field '{key}' bukan angka: {value!r}") from exc


def _outcome_label_index(labels: list[Any], wanted: str) -> int:
    """Cari indeks label outcome (case-insensitive). -1 bila tak ada."""
    for idx, label in enumerate(labels):
        if str(label).strip().lower() == wanted:
            return idx
    return -1


def _derive_status_and_outcome(
    data: dict[str, Any],
    labels: list[Any],
) -> tuple[MarketStatus, Outcome | None]:
    """Tentukan status (open/closed/resolved) + outcome pemenang bila ada."""
    closed = bool(data.get("closed", False))
    active = bool(data.get("active", False))

    winner: Outcome | None = None
    prices_raw = data.get("outcomePrices")
    if prices_raw is not None:
        prices = _json_list(prices_raw, "outcomePrices")
        # Pemenang = harga == 1 (token settle ke $1). Hanya valid saat resolved.
        for idx, price in enumerate(prices):
            try:
                if Decimal(str(price)) >= Decimal("0.999") and idx < len(labels):
                    label = str(labels[idx]).strip().lower()
                    if label == "up":
                        winner = Outcome.UP
                    elif label == "down":
                        winner = Outcome.DOWN
                    break
            except (InvalidOperation, ValueError):
                continue

    if winner is not None:
        return MarketStatus.RESOLVED, winner
    if closed:
        return MarketStatus.CLOSED, None
    if active:
        return MarketStatus.OPEN, None
    # Tidak active & tidak closed → anggap upcoming/open (akan datang).
    return MarketStatus.OPEN, None


def parse_market(data: dict[str, Any]) -> RoundMeta:
    """Petakan satu objek market Gamma → :class:`RoundMeta` (strict).

    Raises:
        GammaSchemaError: bila field wajib hilang/tak terbaca, atau outcomes
            bukan Up/Down (token tak dapat dipetakan dengan aman).
    """
    labels = _json_list(_require(data, "outcomes"), "outcomes")
    token_ids = _json_list(_require(data, "clobTokenIds"), "clobTokenIds")

    idx_up = _outcome_label_index(labels, "up")
    idx_down = _outcome_label_index(labels, "down")
    if idx_up < 0 or idx_down < 0:
        raise GammaSchemaError(f"outcomes bukan Up/Down: {labels!r}")
    if idx_up >= len(token_ids) or idx_down >= len(token_ids):
        raise GammaSchemaError(f"jumlah clobTokenIds ({len(token_ids)}) tak cocok dengan outcomes")

    status, outcome = _derive_status_and_outcome(data, labels)

    return RoundMeta(
        market_id=str(_require(data, "id")),
        condition_id=str(_require(data, "conditionId")),
        slug=str(data.get("slug", "")),
        token_id_up=str(token_ids[idx_up]),
        token_id_down=str(token_ids[idx_down]),
        start_time=_parse_utc(str(_require(data, "startDate")), "startDate"),
        end_time=_parse_utc(str(_require(data, "endDate")), "endDate"),
        tick_size=_to_decimal(_require(data, "orderPriceMinTickSize"), "orderPriceMinTickSize"),
        min_order_size=_to_decimal(_require(data, "orderMinSize"), "orderMinSize"),
        status=status,
        outcome=outcome,
    )


def is_btc5m_market(
    data: dict[str, Any],
    *,
    asset_keywords: tuple[str, ...] = DEFAULT_ASSET_KEYWORDS,
    timeframe_sec: int = DEFAULT_TIMEFRAME_SEC,
    tolerance_sec: int = DEFAULT_TOLERANCE_SEC,
) -> bool:
    """True bila market lolos kriteria seri BTC 5-minute (defensif).

    Mengembalikan ``False`` (bukan raise) bila field untuk evaluasi
    hilang/tak terbaca — sehingga discovery tidak crash pada market lain.
    """
    # 1) Up/Down market
    try:
        labels = _json_list(data.get("outcomes", ""), "outcomes")
    except GammaSchemaError:
        return False
    lowered = {str(label).strip().lower() for label in labels}
    if lowered != {"up", "down"}:
        return False

    # 2) Aset cocok (slug atau question)
    haystack = f"{data.get('slug', '')} {data.get('question', '')}".lower()
    if not any(kw in haystack for kw in asset_keywords):
        return False

    # 3) Durasi window ≈ timeframe (sinyal '5 menit' paling andal)
    start_raw = data.get("startDate")
    end_raw = data.get("endDate")
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        return False
    try:
        start = _parse_utc(start_raw, "startDate")
        end = _parse_utc(end_raw, "endDate")
    except GammaSchemaError:
        return False
    duration = (end - start).total_seconds()
    return abs(duration - timeframe_sec) <= tolerance_sec


class HttpGammaClient:
    """Implementasi :class:`GammaClient` (Gamma REST publik, read-only).

    Args:
        base_url: Base URL Gamma (Settings.gamma_base_url).
        client: ``httpx.AsyncClient`` opsional (untuk test). Bila None dibuat
            internal & ditutup oleh :meth:`aclose`.
        clock: Sumber waktu untuk memilih ronde aktif (default SystemClock).
        timeout: Timeout request (detik).
        page_limit: Ukuran halaman ``limit`` per request.
        max_pages: Batas halaman (anti loop tak hingga).
        max_retries: Percobaan ulang saat 429/5xx/transport error.
        sleep: Fungsi tidur backoff (injectable untuk test).
        asset_keywords / timeframe_sec / tolerance_sec: parameter filter.
    """

    def __init__(  # noqa: PLR0913
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_retries: int = 3,
        sleep: SleepFunc = asyncio.sleep,
        asset_keywords: tuple[str, ...] = DEFAULT_ASSET_KEYWORDS,
        timeframe_sec: int = DEFAULT_TIMEFRAME_SEC,
        tolerance_sec: int = DEFAULT_TOLERANCE_SEC,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._clock = clock or SystemClock()
        self._page_limit = page_limit
        self._max_pages = max_pages
        self._max_retries = max(1, max_retries)
        self._sleep = sleep
        self._asset_keywords = asset_keywords
        self._timeframe_sec = timeframe_sec
        self._tolerance_sec = tolerance_sec

    async def discover_rounds(self) -> list[RoundMeta]:
        """Kumpulkan semua ronde BTC 5m aktif/akan datang, terurut start_time."""
        rounds: list[RoundMeta] = []
        for page in range(self._max_pages):
            params = {
                "active": "true",
                "closed": "false",
                "limit": str(self._page_limit),
                "offset": str(page * self._page_limit),
            }
            batch = await self._get_markets(params)
            if not batch:
                break
            for raw in batch:
                if not isinstance(raw, dict):
                    continue
                if not is_btc5m_market(
                    raw,
                    asset_keywords=self._asset_keywords,
                    timeframe_sec=self._timeframe_sec,
                    tolerance_sec=self._tolerance_sec,
                ):
                    continue
                rounds.append(parse_market(raw))
            if len(batch) < self._page_limit:
                break
        rounds.sort(key=lambda r: r.start_time)
        return rounds

    async def discover_active_round(self) -> RoundMeta:
        """Pilih ronde yang window-nya mencakup ``now``; jika tidak, terdekat.

        Raises:
            GammaError: bila tidak ada ronde BTC 5m yang ditemukan.
        """
        rounds = await self.discover_rounds()
        if not rounds:
            raise GammaError("tidak ada ronde BTC 5m ditemukan dari Gamma")
        now = self._clock.now()
        # 1) window aktif (start <= now < end)
        for rnd in rounds:
            if rnd.start_time <= now < rnd.end_time:
                return rnd
        # 2) terdekat akan datang (start >= now)
        upcoming = [r for r in rounds if r.start_time >= now]
        if upcoming:
            return min(upcoming, key=lambda r: r.start_time)
        # 3) paling akhir (fallback)
        return max(rounds, key=lambda r: r.start_time)

    async def get_market(self, condition_id: str) -> RoundMeta:
        """Ambil satu market berdasarkan ``condition_id``.

        Raises:
            GammaError: bila market tidak ditemukan.
        """
        batch = await self._get_markets({"condition_ids": condition_id})
        for raw in batch:
            if isinstance(raw, dict):
                return parse_market(raw)
        raise GammaError(f"market dengan condition_id={condition_id} tidak ditemukan")

    async def _get_markets(self, params: dict[str, str]) -> list[Any]:
        """GET ``/markets`` dengan retry + backoff; kembalikan list JSON."""
        backoff = 0.5
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.get(ENDPOINT_MARKETS, params=params)
            except httpx.TransportError as exc:
                last_exc = exc
                await self._sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == httpx.codes.TOO_MANY_REQUESTS or (
                resp.status_code >= _HTTP_SERVER_ERROR
            ):
                last_exc = httpx.HTTPStatusError(
                    f"status {resp.status_code}", request=resp.request, response=resp
                )
                if attempt < self._max_retries - 1:
                    await self._sleep(backoff)
                    backoff *= 2
                    continue
                raise GammaError(f"Gamma /markets gagal: status {resp.status_code}") from last_exc
            resp.raise_for_status()
            payload = resp.json()
            # Gamma dapat mengembalikan list langsung, atau objek ber-"data".
            if isinstance(payload, list):
                return list(payload)
            if isinstance(payload, dict):
                data = payload.get("data")
                if isinstance(data, list):
                    return list(data)
            return []
        raise GammaError("Gamma /markets gagal setelah retry") from last_exc

    async def aclose(self) -> None:
        """Tutup client internal (no-op bila client diinjeksi)."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> HttpGammaClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
