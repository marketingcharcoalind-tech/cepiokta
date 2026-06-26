"""Gamma API adapter — discovery market Up/Down (docs/04 §4.3, docs/08 §8.2).

Membaca market dari **Gamma REST API publik** (read-only, tanpa auth, tanpa
order) lalu memetakan ke :class:`~btcbot.domain.models.RoundMeta`.

Temuan dari data **live** (sumber kebenaran, diverifikasi di VPS):

- Market up/down diidentifikasi dari **slug**, bukan teks judul:
  ``^(?P<asset>[a-z]+)-updown-(?P<tf>5m|15m)-(?P<epoch>\\d+)$``
  (mis. ``btc-updown-5m-1782472800``).
- ``epoch`` di slug = **waktu resolusi** (unix) = ``endDate`` = ``window_end``.
  Sanity: 5m kelipatan 300, 15m kelipatan 900.
- ``window_start`` = ``eventStartTime`` (atau ``events[0].startTime``); fallback
  = ``epoch - tf`` (300/900). **JANGAN** pakai ``startDate`` (tanggal listing
  ~24 jam lebih awal — sumber bug filter durasi lama).
- ``outcomes`` = ``["Up","Down"]``; ``clobTokenIds`` dipetakan **sejajar index**
  dengan ``outcomes`` (Up & Down tidak tertukar).
- ``tick_size`` ← ``orderPriceMinTickSize``; ``min_order_size`` ← ``orderMinSize``.
- ``resolutionSource`` → resolusi via **Chainlink Data Streams** (disimpan).
- **FEE**: ``feesEnabled``, ``feeType`` (``crypto_fees_v2``), ``feeSchedule``
  ``{exponent, rate, takerOnly, rebateRate}`` di-parse & dibawa ke model.
- ``outcomePrices`` **STALE** untuk market cepat → TIDAK dipakai sebagai harga
  (harga live tetap dari order book CLOB).

Strategi query: gunakan jendela ``end_date`` (bukan /markets default yang
ke-cap & diurut volume → market 5m tenggelam).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from btcbot.adapters.clock import Clock, SystemClock
from btcbot.domain.models import FeeSchedule, MarketStatus, Outcome, RoundMeta

if TYPE_CHECKING:
    from types import TracebackType

ENDPOINT_MARKETS = "/markets"
DEFAULT_TIMEOUT = 10.0
DEFAULT_PAGE_LIMIT = 500
DEFAULT_MAX_PAGES = 10
_HTTP_SERVER_ERROR = 500

# Regex identifikasi market up/down dari slug.
SLUG_RE = re.compile(r"^(?P<asset>[a-z]+)-updown-(?P<tf>5m|15m)-(?P<epoch>\d+)$")

TIMEFRAME_SECONDS: dict[str, int] = {"5m": 300, "15m": 900}
# Jendela end_date untuk discovery (cukup menampung ronde aktif + berikutnya).
DISCOVERY_BUFFER_SECONDS: dict[str, int] = {"5m": 12 * 60, "15m": 30 * 60}

DEFAULT_ASSET = "btc"
DEFAULT_TIMEFRAME = "5m"

# Gamma menolak User-Agent default httpx (403). Pakai UA browser.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SleepFunc = Callable[[float], Awaitable[None]]


class GammaError(Exception):
    """Kesalahan umum adapter Gamma."""


class GammaSchemaError(GammaError):
    """Respons Gamma tidak sesuai skema (field wajib hilang/tak terbaca)."""


class GammaClient(Protocol):
    """Kontrak discovery market (docs/08 §8.2)."""

    async def discover_active_round(self) -> RoundMeta:
        """Kembalikan ronde yang aktif kini / terdekat akan datang."""
        ...

    async def discover_rounds(self) -> list[RoundMeta]:
        """Kembalikan daftar ronde (untuk penjadwalan scanner, Fase 1+)."""
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


def match_updown_slug(slug: str) -> tuple[str, str, int] | None:
    """Cocokkan slug up/down → ``(asset, timeframe, epoch)`` atau ``None``.

    Mengembalikan ``None`` bila slug bukan format up/down atau epoch tidak
    kelipatan timeframe (sanity).
    """
    m = SLUG_RE.match(slug)
    if m is None:
        return None
    asset = m.group("asset")
    timeframe = m.group("tf")
    epoch = int(m.group("epoch"))
    if epoch % TIMEFRAME_SECONDS[timeframe] != 0:
        return None
    return asset, timeframe, epoch


def is_updown_market(data: dict[str, Any], asset: str, timeframe: str) -> bool:
    """True bila market adalah seri up/down untuk ``asset`` & ``timeframe``."""
    slug = data.get("slug")
    if not isinstance(slug, str):
        return False
    parsed = match_updown_slug(slug)
    if parsed is None:
        return False
    found_asset, found_tf, _ = parsed
    return found_asset == asset.lower() and found_tf == timeframe


def _window_end(data: dict[str, Any], epoch: int) -> datetime:
    """``window_end`` = ``endDate`` bila ada, jika tidak dari slug epoch."""
    end_raw = data.get("endDate")
    if isinstance(end_raw, str) and end_raw:
        return _parse_utc(end_raw, "endDate")
    return datetime.fromtimestamp(epoch, tz=UTC)


def _window_start(data: dict[str, Any], epoch: int, tf_seconds: int) -> datetime:
    """``window_start`` = ``eventStartTime``/``events[0].startTime`` else epoch-tf.

    JANGAN memakai ``startDate`` (tanggal listing).
    """
    ev_start = data.get("eventStartTime")
    if not isinstance(ev_start, str) or not ev_start:
        events = data.get("events")
        if isinstance(events, list) and events and isinstance(events[0], dict):
            candidate = events[0].get("startTime")
            ev_start = candidate if isinstance(candidate, str) else None
    if isinstance(ev_start, str) and ev_start:
        return _parse_utc(ev_start, "eventStartTime")
    return datetime.fromtimestamp(epoch - tf_seconds, tz=UTC)


def _parse_fee_schedule(raw: object) -> FeeSchedule | None:
    """Parse ``feeSchedule`` → :class:`FeeSchedule` (None bila absen)."""
    if not isinstance(raw, dict):
        return None
    return FeeSchedule(
        exponent=int(_require(raw, "exponent")),
        rate=_to_decimal(_require(raw, "rate"), "rate"),
        taker_only=bool(_require(raw, "takerOnly")),
        rebate_rate=_to_decimal(_require(raw, "rebateRate"), "rebateRate"),
    )


def parse_market(data: dict[str, Any]) -> RoundMeta:
    """Petakan satu objek market Gamma → :class:`RoundMeta` (strict).

    Raises:
        GammaSchemaError: bila bukan market up/down, field wajib hilang/tak
            terbaca, atau token Up/Down tak dapat dipetakan dengan aman.
    """
    slug = str(_require(data, "slug"))
    parsed = match_updown_slug(slug)
    if parsed is None:
        raise GammaSchemaError(f"slug bukan format up/down valid: {slug!r}")
    asset, timeframe, epoch = parsed
    tf_seconds = TIMEFRAME_SECONDS[timeframe]

    labels = _json_list(_require(data, "outcomes"), "outcomes")
    token_ids = _json_list(_require(data, "clobTokenIds"), "clobTokenIds")
    idx_up = _outcome_label_index(labels, "up")
    idx_down = _outcome_label_index(labels, "down")
    if idx_up < 0 or idx_down < 0:
        raise GammaSchemaError(f"outcomes bukan Up/Down: {labels!r}")
    if idx_up >= len(token_ids) or idx_down >= len(token_ids):
        raise GammaSchemaError(f"jumlah clobTokenIds ({len(token_ids)}) tak cocok dengan outcomes")

    status = MarketStatus.CLOSED if bool(data.get("closed", False)) else MarketStatus.OPEN

    resolution_source = data.get("resolutionSource")
    fee_type = data.get("feeType")

    return RoundMeta(
        market_id=str(_require(data, "id")),
        condition_id=str(_require(data, "conditionId")),
        slug=slug,
        token_id_up=str(token_ids[idx_up]),
        token_id_down=str(token_ids[idx_down]),
        start_time=_window_start(data, epoch, tf_seconds),
        end_time=_window_end(data, epoch),
        tick_size=_to_decimal(_require(data, "orderPriceMinTickSize"), "orderPriceMinTickSize"),
        min_order_size=_to_decimal(_require(data, "orderMinSize"), "orderMinSize"),
        status=status,
        outcome=None,  # outcomePrices STALE untuk market cepat → resolusi via Chainlink (Fase 1)
        asset=asset,
        timeframe=timeframe,
        resolution_source=str(resolution_source) if resolution_source is not None else None,
        fees_enabled=bool(data.get("feesEnabled", False)),
        fee_type=str(fee_type) if fee_type is not None else None,
        fee_schedule=_parse_fee_schedule(data.get("feeSchedule")),
    )


def _iso_z(dt: datetime) -> str:
    """Format datetime → ISO-8601 UTC dengan sufiks ``Z`` (param Gamma)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# Ambang harga outcome pemenang/pecundang (token settle ke $1 / $0).
_WINNER_PRICE = Decimal("0.99")
_LOSER_PRICE = Decimal("0.01")


def parse_resolution(data: dict[str, Any]) -> Outcome | None:  # noqa: PLR0911 - guard clauses
    """Tentukan outcome RESOLVED (ground truth) dari market Gamma.

    Sumber kebenaran = ``outcomePrices`` (JSON-encoded string, mis. ``"[\"1\",\"0\"]"``).
    Resolved HANYA bila ``closed is true`` DAN harga definitif: tepat satu outcome
    ``>= 0.99`` dan sisanya ``<= 0.01``. ``umaResolutionStatus`` TIDAK dijadikan
    syarat. Pemenang = ``outcomes[index_yang_bernilai_1]`` (Up/Down, case-insensitive).

    Returns:
        :class:`Outcome` (UP/DOWN), atau ``None`` bila belum resolved / ambigu /
        token bukan Up/Down.
    """
    if not bool(data.get("closed", False)):
        return None

    prices_raw = data.get("outcomePrices")
    if prices_raw is None:
        return None
    try:
        prices = _json_list(prices_raw, "outcomePrices")
        labels = _json_list(data.get("outcomes") or "[]", "outcomes")
    except GammaSchemaError:
        return None

    winners: list[int] = []
    for idx, raw_price in enumerate(prices):
        try:
            price = Decimal(str(raw_price))
        except (InvalidOperation, ValueError):
            return None  # harga tak terbaca → jangan tebak
        if price >= _WINNER_PRICE:
            winners.append(idx)
        elif price > _LOSER_PRICE:
            return None  # nilai menengah (mis. 0.5) → belum definitif

    if len(winners) != 1:
        return None
    idx = winners[0]
    if idx >= len(labels):
        return None
    label = str(labels[idx]).strip().lower()
    if label == "up":
        return Outcome.UP
    if label == "down":
        return Outcome.DOWN
    return None


class HttpGammaClient:
    """Implementasi :class:`GammaClient` (Gamma REST publik, read-only).

    Satu instance melayani satu ``(asset, timeframe)`` (sejalan desain
    per-market worker Fase 1+).

    Args:
        base_url: Base URL Gamma (Settings.gamma_base_url).
        client: ``httpx.AsyncClient`` opsional (untuk test).
        clock: Sumber waktu untuk window & jendela query (default SystemClock).
        asset: Aset target (default ``btc``).
        timeframe: ``5m`` | ``15m`` (default ``5m``).
        timeout / page_limit / max_pages / max_retries / sleep: HTTP & retry.
    """

    def __init__(  # noqa: PLR0913
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
        asset: str = DEFAULT_ASSET,
        timeframe: str = DEFAULT_TIMEFRAME,
        timeout: float = DEFAULT_TIMEOUT,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_retries: int = 3,
        sleep: SleepFunc = asyncio.sleep,
    ) -> None:
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"timeframe tak didukung: {timeframe!r} (pilih 5m/15m)")
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={"User-Agent": BROWSER_USER_AGENT},
        )
        self._clock = clock or SystemClock()
        self._asset = asset.lower()
        self._timeframe = timeframe
        self._page_limit = page_limit
        self._max_pages = max_pages
        self._max_retries = max(1, max_retries)
        self._sleep = sleep

    async def discover_rounds(self) -> list[RoundMeta]:
        """Kumpulkan ronde up/down (asset/timeframe) via jendela end_date."""
        now = self._clock.now()
        buffer_sec = DISCOVERY_BUFFER_SECONDS[self._timeframe]
        base_params = {
            "closed": "false",
            "active": "true",
            "end_date_min": _iso_z(now),
            "end_date_max": _iso_z(now + timedelta(seconds=buffer_sec)),
            "limit": str(self._page_limit),
        }
        rounds: list[RoundMeta] = []
        for page in range(self._max_pages):
            params = dict(base_params)
            params["offset"] = str(page * self._page_limit)
            batch = await self._get_markets(params)
            if not batch:
                break
            for raw in batch:
                if isinstance(raw, dict) and is_updown_market(raw, self._asset, self._timeframe):
                    rounds.append(parse_market(raw))
            if len(batch) < self._page_limit:
                break
        rounds.sort(key=lambda r: r.start_time)
        return rounds

    async def discover_active_round(self) -> RoundMeta:
        """Pilih ronde in-window (start<=now<end); jika tidak, berikutnya.

        Raises:
            GammaError: bila tidak ada ronde ditemukan.
        """
        rounds = await self.discover_rounds()
        if not rounds:
            raise GammaError(
                f"tidak ada ronde {self._asset}-updown-{self._timeframe} ditemukan dari Gamma"
            )
        now = self._clock.now()
        for rnd in rounds:
            if rnd.start_time <= now < rnd.end_time:
                return rnd
        upcoming = [r for r in rounds if r.start_time > now]
        if upcoming:
            return min(upcoming, key=lambda r: r.start_time)
        return max(rounds, key=lambda r: r.start_time)

    async def get_market(self, condition_id: str) -> RoundMeta:
        """Ambil satu market berdasarkan ``condition_id``.

        Raises:
            GammaError: bila market tidak ditemukan.
        """
        batch = await self._get_markets({"condition_ids": condition_id})
        for raw in batch:
            if isinstance(raw, dict) and raw.get("conditionId") == condition_id:
                return parse_market(raw)
        # fallback: market pertama yang merupakan up/down
        for raw in batch:
            if isinstance(raw, dict):
                return parse_market(raw)
        raise GammaError(f"market dengan condition_id={condition_id} tidak ditemukan")

    async def fetch_resolved_market(
        self,
        *,
        slug: str | None = None,
        condition_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Ambil market mentah yang SUDAH closed (``closed=true``) by slug/condition.

        Gamma ``/markets`` default membuang market closed → resolusi WAJIB
        memakai ``closed=true``. Kembalikan dict mentah pertama yang cocok, atau
        ``None`` bila tak ada.

        Raises:
            ValueError: bila ``slug`` & ``condition_id`` keduanya kosong.
        """
        if not slug and not condition_id:
            raise ValueError("fetch_resolved_market butuh slug atau condition_id")
        params: dict[str, str] = {"closed": "true", "limit": str(self._page_limit)}
        if condition_id:
            params["condition_ids"] = condition_id
        if slug:
            params["slug"] = slug
        batch = await self._get_markets(params)
        for raw in batch:
            if not isinstance(raw, dict):
                continue
            if condition_id and raw.get("conditionId") == condition_id:
                return raw
            if slug and raw.get("slug") == slug:
                return raw
        for raw in batch:
            if isinstance(raw, dict):
                return raw
        return None

    async def get_resolution(self, condition_id: str) -> Outcome | None:
        """Ambil outcome RESOLVED (ground truth) market via ``condition_id``.

        Query ``/markets?...&closed=true`` (market resolved dibuang dari default),
        lalu parse via :func:`parse_resolution`. ``None`` bila belum resolved /
        tak ditemukan (resolver akan coba lagi nanti).
        """
        raw = await self.fetch_resolved_market(condition_id=condition_id)
        return parse_resolution(raw) if raw is not None else None

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
