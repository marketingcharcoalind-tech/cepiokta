"""backtest/replay.py — ReplayEngine + fill model (docs/09 §9.3, docs/08 §8.13).

Putar ulang data terekam Fase 0 (``rounds`` + ``book_snapshots`` + ``signals``)
melalui pipeline domain murni: **SignalEngine → Strategy → Sizer → fill model**,
memakai :class:`~btcbot.adapters.clock.SimClock`. Tulis ``round_results`` &
``equity_curve`` (``mode=backtest``).

Properti kunci (PROMPT_GUIDE ✅ VERIFIED REALITY #3,#5,#6):

- **Fee taker ~7%** (``crypto_fees_v2``) dikurangi pada setiap fill (realized),
  selain sudah diperhitungkan di ``net_edge`` saat keputusan (estimasi).
- **Slippage menelusuri level book** (fill menelan level dari best ke dalam).
- **Latensi**: keputusan pakai book tick ``t``, fill pakai book tick ``t+latency``.
- **Kompetisi**: hanya *surplus* depth yang bisa diisi (``1 - competition_fraction``).
- **Settlement** memakai label **UP/DOWN dari Gamma** (``round.resolved_outcome``),
  bukan asumsi Δ.
- Book input = recorded (best/ depth → rekonstruksi), **last-value-carried-forward**.

Determinisme (DoD): seed tetap → PnL reproducible. Model fill bersifat
deterministik-by-construction; ``seed`` disimpan & RNG di-inject untuk
reproducibility bila opsi stokastik ditambah kemudian.
"""

from __future__ import annotations

import bisect
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from btcbot.adapters.clock import SimClock
from btcbot.domain.fees import FeeModel, ProportionalTakerFee
from btcbot.domain.models import (
    BookLevel,
    Fill,
    OrderBook,
    Outcome,
    Position,
    Round,
    RoundResult,
    Signal,
)
from btcbot.domain.signal import SignalEngine
from btcbot.domain.strategy import (
    SIDE_BUY,
    EnterOrder,
    Exit,
    Hedge,
    MarketBook,
    NoOp,
    Strategy,
    StrategyParams,
)
from btcbot.exec.sizing import (
    SIZING_BINDING_KEYS,
    SIZING_CLASS_KEYS,
    SizingDiagnostic,
    SizingLimits,
    diagnose_size,
    round_to_tick,
    size,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence
    from datetime import datetime

    from btcbot.config.settings import Settings
    from btcbot.data.store import BookSnapshot, Store

_ZERO = Decimal("0")
_ONE = Decimal("1")
_EPS = Decimal("1e-9")


# ----- fill model -----


@dataclass(frozen=True, slots=True)
class FillResult:
    """Hasil simulasi fill (taker) menelusuri level book."""

    filled_size: Decimal
    avg_price: Decimal  # 0 bila tak ada yang terisi
    notional: Decimal  # filled_size * avg_price

    @property
    def filled(self) -> bool:
        return self.filled_size > _ZERO


_NO_FILL = FillResult(filled_size=_ZERO, avg_price=_ZERO, notional=_ZERO)


def simulate_fill(  # noqa: PLR0913, PLR0911 - parameter & guard eksplisit
    *,
    book: OrderBook,
    side: str,
    limit_price: Decimal,
    requested_size: Decimal,
    order_type: str,
    competition_fraction: Decimal = _ZERO,
    ignore_depth: bool = False,
) -> FillResult:
    """Simulasikan eksekusi taker pada ``book`` (murni, deterministik).

    Menelusuri level (best → dalam): BUY menelan ``asks`` (harga ascending,
    ``price <= limit``), SELL menelan ``bids`` (harga descending,
    ``price >= limit``). Hanya *surplus* depth tersedia: ``size*(1-competition)``.
    ``FOK`` = all-or-nothing (gagal bila tak penuh); ``FAK`` = partial diizinkan.

    Args:
        book: Order book pada saat eksekusi (tick t+latency).
        side: ``"BUY"`` | ``"SELL"``.
        limit_price: Harga limit (taker tidak akan melewati ini).
        requested_size: Ukuran diminta (share).
        order_type: ``"FOK"`` | ``"FAK"``.
        competition_fraction: Fraksi depth diambil bot lain ([0,1)).
        ignore_depth: Bila True → isi penuh di harga best (ablation **tanpa
            slippage**: likuiditas dianggap tak terbatas di best in-range).
    """
    if requested_size <= _ZERO:
        return _NO_FILL
    factor = _ONE - competition_fraction
    is_buy = side == SIDE_BUY
    if is_buy:
        levels = sorted(book.asks, key=lambda lvl: lvl.price)
    else:
        levels = sorted(book.bids, key=lambda lvl: lvl.price, reverse=True)

    if ignore_depth:
        if not levels:
            return _NO_FILL
        best = levels[0]
        if (is_buy and best.price > limit_price) or (not is_buy and best.price < limit_price):
            return _NO_FILL
        return FillResult(
            filled_size=requested_size,
            avg_price=best.price,
            notional=requested_size * best.price,
        )

    remaining = requested_size
    filled = _ZERO
    cost = _ZERO
    for lvl in levels:
        if (is_buy and lvl.price > limit_price) or (not is_buy and lvl.price < limit_price):
            break
        available = lvl.size * factor
        if available <= _ZERO:
            continue
        take = min(remaining, available)
        filled += take
        cost += take * lvl.price
        remaining -= take
        if remaining <= _EPS:
            break

    # FOK: harus terisi penuh (toleransi epsilon) atau batal.
    if order_type == "FOK" and filled + _EPS < requested_size:
        return _NO_FILL
    if filled <= _ZERO:
        return _NO_FILL
    return FillResult(filled_size=filled, avg_price=cost / filled, notional=cost)


# ----- fill-failure classification (Task G3 — observability MURNI, read-only) -----

FILL_FAIL_EMPTY_BOOK = "EMPTY_BOOK"
FILL_FAIL_BEST_ABOVE_LIMIT = "BEST_PRICE_ABOVE_LIMIT"
FILL_FAIL_NO_LEVEL_WITHIN_LIMIT = "NO_LEVEL_WITHIN_LIMIT"
FILL_FAIL_INSUFFICIENT_DEPTH_FOK = "INSUFFICIENT_DEPTH_FOK"
FILL_FAIL_REQUESTED_SIZE_ZERO = "REQUESTED_SIZE_ZERO"
FILL_FAIL_UNKNOWN = "UNKNOWN"
FILL_FAIL_KEYS: tuple[str, ...] = (
    FILL_FAIL_EMPTY_BOOK,
    FILL_FAIL_BEST_ABOVE_LIMIT,
    FILL_FAIL_NO_LEVEL_WITHIN_LIMIT,
    FILL_FAIL_INSUFFICIENT_DEPTH_FOK,
    FILL_FAIL_REQUESTED_SIZE_ZERO,
    FILL_FAIL_UNKNOWN,
)


def _new_fill_failures() -> dict[str, int]:
    return dict.fromkeys(FILL_FAIL_KEYS, 0)


def classify_no_fill(  # noqa: PLR0911, PLR0913 - guard eksplisit per-reason
    *,
    book: OrderBook,
    side: str,
    limit_price: Decimal,
    requested_size: Decimal,
    order_type: str,
    competition_fraction: Decimal = _ZERO,
    ignore_depth: bool = False,
) -> str:
    """Klasifikasikan SEBAB sebuah taker order tidak terisi (NO_FILL).

    **Read-only & cermin** :func:`simulate_fill` (semantik level-walk identik) —
    TIDAK mengubah perilaku fill. Dipanggil HANYA saat fill = 0 untuk observability
    (Task G3). Kembalikan tepat satu dari :data:`FILL_FAIL_KEYS`.
    """
    if requested_size <= _ZERO:
        return FILL_FAIL_REQUESTED_SIZE_ZERO
    is_buy = side == SIDE_BUY
    levels = (
        sorted(book.asks, key=lambda lvl: lvl.price)
        if is_buy
        else sorted(book.bids, key=lambda lvl: lvl.price, reverse=True)
    )
    if not levels:
        return FILL_FAIL_EMPTY_BOOK
    best = levels[0]
    if (is_buy and best.price > limit_price) or (not is_buy and best.price < limit_price):
        return FILL_FAIL_BEST_ABOVE_LIMIT
    if ignore_depth:
        # best in-range → simulate_fill mengisi penuh; no-fill mustahil di sini.
        return FILL_FAIL_UNKNOWN
    factor = _ONE - competition_fraction
    avail = _ZERO
    for lvl in levels:
        in_range = lvl.price <= limit_price if is_buy else lvl.price >= limit_price
        if not in_range:
            break
        avail += lvl.size * factor
    if avail <= _ZERO:
        return FILL_FAIL_NO_LEVEL_WITHIN_LIMIT
    if order_type == "FOK" and avail + _EPS < requested_size:
        return FILL_FAIL_INSUFFICIENT_DEPTH_FOK
    return FILL_FAIL_UNKNOWN


# ----- sizing diagnostics aggregation (Task G4 — observability MURNI) -----
#
# Statistik streaming memori-konstan: min/max/mean eksak + kuantil P-square
# (Jain & Chlamtac 1985), deterministik per urutan input. TIDAK menyimpan
# riwayat per-tick (hanya 5 marker/kuantil).

_SIZING_QUANTILES: tuple[float, ...] = (0.25, 0.5, 0.75)


def _new_sizing_binding() -> dict[str, int]:
    return dict.fromkeys(SIZING_BINDING_KEYS, 0)


def _new_sizing_class() -> dict[str, int]:
    return dict.fromkeys(SIZING_CLASS_KEYS, 0)


# Ratio buckets (Task G5): histogram memori-konstan untuk rasio depth/min &
# raw/min. Ambang 1.0 = batas "satu min-order" (di bawahnya = terlalu kecil).
RATIO_BUCKET_KEYS: tuple[str, ...] = (
    "<0.1",
    "0.1-0.5",
    "0.5-1",
    "1-2",
    "2-5",
    "5-10",
    ">=10",
)


def _new_ratio_buckets() -> dict[str, int]:
    return dict.fromkeys(RATIO_BUCKET_KEYS, 0)


_RATIO_EDGES: tuple[tuple[Decimal, str], ...] = (
    (Decimal("0.1"), "<0.1"),
    (Decimal("0.5"), "0.1-0.5"),
    (_ONE, "0.5-1"),
    (Decimal("2"), "1-2"),
    (Decimal("5"), "2-5"),
    (Decimal("10"), "5-10"),
)


def _ratio_bucket(ratio: Decimal) -> str:
    """Petakan rasio (>= 0) ke salah satu :data:`RATIO_BUCKET_KEYS`."""
    for edge, label in _RATIO_EDGES:
        if ratio < edge:
            return label
    return ">=10"


@dataclass(frozen=True, slots=True)
class SizingStat:
    """Ringkasan distribusi sizing (share/cap). Kuantil = estimasi P-square."""

    count: int = 0
    minimum: Decimal = _ZERO
    p25: Decimal = _ZERO
    median: Decimal = _ZERO
    mean: Decimal = _ZERO
    p75: Decimal = _ZERO
    maximum: Decimal = _ZERO


class _PSquareQuantile:
    """Estimator kuantil-tunggal P-square (constant memory; 5 marker)."""

    def __init__(self, p: float) -> None:
        self._p = p
        self._init: list[float] = []
        self._q: list[float] = []  # tinggi marker
        self._n: list[float] = []  # posisi aktual
        self._np: list[float] = []  # posisi diinginkan
        self._dn: list[float] = []  # increment posisi diinginkan
        self._ready = False

    def add(self, x: float) -> None:
        if not self._ready:
            self._init.append(x)
            if len(self._init) == 5:  # noqa: PLR2004 - 5 marker P-square
                self._init.sort()
                self._q = list(self._init)
                self._n = [1.0, 2.0, 3.0, 4.0, 5.0]
                p = self._p
                self._np = [1.0, 1.0 + 2.0 * p, 1.0 + 4.0 * p, 3.0 + 2.0 * p, 5.0]
                self._dn = [0.0, p / 2.0, p, (1.0 + p) / 2.0, 1.0]
                self._ready = True
            return
        self._observe(x)

    def _observe(self, x: float) -> None:
        q = self._q
        if x < q[0]:
            q[0] = x
            k = 0
        elif x >= q[4]:
            q[4] = x
            k = 3
        else:
            k = 3
            for i in range(1, 5):
                if x < q[i]:
                    k = i - 1
                    break
        for i in range(k + 1, 5):
            self._n[i] += 1.0
        for i in range(5):
            self._np[i] += self._dn[i]
        for i in range(1, 4):
            self._adjust(i)

    def _adjust(self, i: int) -> None:
        d = self._np[i] - self._n[i]
        gap_up = self._n[i + 1] - self._n[i]
        gap_dn = self._n[i - 1] - self._n[i]
        if (d >= 1.0 and gap_up > 1.0) or (d <= -1.0 and gap_dn < -1.0):
            sign = 1.0 if d > 0 else -1.0
            qp = self._parabolic(i, sign)
            if self._q[i - 1] < qp < self._q[i + 1]:
                self._q[i] = qp
            else:
                self._q[i] = self._linear(i, sign)
            self._n[i] += sign

    def _parabolic(self, i: int, d: float) -> float:
        q, n = self._q, self._n
        return q[i] + d / (n[i + 1] - n[i - 1]) * (
            (n[i] - n[i - 1] + d) * (q[i + 1] - q[i]) / (n[i + 1] - n[i])
            + (n[i + 1] - n[i] - d) * (q[i] - q[i - 1]) / (n[i] - n[i - 1])
        )

    def _linear(self, i: int, d: float) -> float:
        j = i + int(d)
        return self._q[i] + d * (self._q[j] - self._q[i]) / (self._n[j] - self._n[i])

    def value(self) -> float:
        if self._ready:
            return self._q[2]
        if not self._init:
            return 0.0
        s = sorted(self._init)
        pos = self._p * (len(s) - 1)
        lo = int(pos)
        frac = pos - lo
        if lo + 1 < len(s):
            return s[lo] + frac * (s[lo + 1] - s[lo])
        return s[lo]


class _StreamStats:
    """Statistik streaming: count/min/max/mean eksak + kuantil P-square."""

    def __init__(self) -> None:
        self._count = 0
        self._sum = _ZERO
        self._min: Decimal | None = None
        self._max: Decimal | None = None
        self._q = {p: _PSquareQuantile(p) for p in _SIZING_QUANTILES}

    def add(self, value: Decimal) -> None:
        self._count += 1
        self._sum += value
        if self._min is None or value < self._min:
            self._min = value
        if self._max is None or value > self._max:
            self._max = value
        xf = float(value)
        for est in self._q.values():
            est.add(xf)

    def summary(self) -> SizingStat:
        if self._count == 0:
            return SizingStat()
        mean = self._sum / Decimal(self._count)
        return SizingStat(
            count=self._count,
            minimum=self._min if self._min is not None else _ZERO,
            p25=Decimal(str(self._q[0.25].value())),
            median=Decimal(str(self._q[0.5].value())),
            mean=mean,
            p75=Decimal(str(self._q[0.75].value())),
            maximum=self._max if self._max is not None else _ZERO,
        )


# ----- replay inputs & config -----


@dataclass(frozen=True, slots=True)
class ReplayTick:
    """Satu tick replay: harga BTC + order book UP/DOWN pada ``ts``."""

    ts: datetime
    btc_price: Decimal
    book_up: OrderBook
    book_down: OrderBook


@dataclass(frozen=True, slots=True)
class ExecutionSelection:
    """Result of execution tick selection for latency modeling.
    
    Represents the deterministic selection of an execution tick given a decision
    tick and latency configuration. Used by both tick-based and time-based modes.
    """

    latency_mode: str  # "ticks" | "time"
    decision_tick_index: int
    
    # Tick mode fields
    requested_execution_tick_index: int | None  # decision_index + latency_ticks
    
    # Time mode fields
    requested_execution_ts: datetime | None  # decision_ts + latency_ms
    
    # Common result fields
    actual_execution_tick_index: int | None  # None if no_future_tick
    actual_execution_ts: datetime | None  # None if no_future_tick
    
    # Status flags
    tick_clamped: bool  # tick mode: requested >= n (clamped to final tick)
    no_future_tick: bool  # time mode: no tick exists at or after requested_ts
    
    # Latency metrics
    configured_latency_ticks: int | None  # tick mode config
    configured_latency_ms: int | None  # time mode config
    realized_latency_ms: float | None  # actual_ts - decision_ts (None if no execution)
    execution_overshoot_ms: float | None  # actual_ts - requested_ts (time mode only)


def select_execution_tick(
    ticks: Sequence[ReplayTick],
    decision_index: int,
    *,
    latency_mode: str,
    latency_ticks: int,
    latency_ms: int,
) -> ExecutionSelection:
    """Pure deterministic execution tick selector for replay latency modeling.
    
    Given a decision tick and latency configuration, selects the execution tick
    according to either tick-based or time-based latency rules.
    
    Tick mode:
        - requested_index = decision_index + latency_ticks
        - actual_index = min(requested_index, n - 1)
        - tick_clamped = requested_index >= n
    
    Time mode:
        - decision_ts = ticks[decision_index].ts
        - requested_ts = decision_ts + timedelta(milliseconds=latency_ms)
        - select first tick where tick.ts >= requested_ts
        - if no such tick exists: no_future_tick = True, no execution
    
    Args:
        ticks: Sequence of replay ticks (must be non-empty, sorted by timestamp)
        decision_index: Index of decision tick (must be valid: 0 <= i < len(ticks))
        latency_mode: "ticks" or "time"
        latency_ticks: Tick offset for tick mode (must be >= 0)
        latency_ms: Milliseconds delay for time mode (must be >= 0)
    
    Returns:
        ExecutionSelection with all computed fields
    
    Raises:
        ValueError: Invalid inputs (empty ticks, invalid index, negative latency,
                    invalid mode, naive timestamps, unsorted timestamps)
    """
    # Validate inputs
    if not ticks:
        raise ValueError("ticks must be non-empty")
    
    n = len(ticks)
    if decision_index < 0 or decision_index >= n:
        raise ValueError(
            f"decision_index {decision_index} out of bounds [0, {n})"
        )
    
    if latency_mode not in ("ticks", "time"):
        raise ValueError(
            f"latency_mode must be 'ticks' or 'time', got '{latency_mode}'"
        )
    
    if latency_ticks < 0:
        raise ValueError(f"latency_ticks must be >= 0, got {latency_ticks}")
    
    if latency_ms < 0:
        raise ValueError(f"latency_ms must be >= 0, got {latency_ms}")
    
    # Validate timestamps are timezone-aware and sorted
    for i, tick in enumerate(ticks):
        if tick.ts.tzinfo is None:
            raise ValueError(
                f"tick {i} has naive timestamp {tick.ts}, all timestamps must be UTC-aware"
            )
        if i > 0 and tick.ts < ticks[i - 1].ts:
            raise ValueError(
                f"ticks not sorted: tick {i} ts={tick.ts} < tick {i-1} ts={ticks[i-1].ts}"
            )
    
    decision_ts = ticks[decision_index].ts
    
    if latency_mode == "ticks":
        # Tick-based mode: preserve exact historical behavior
        requested_index = decision_index + latency_ticks
        actual_index = min(requested_index, n - 1)
        tick_clamped = requested_index >= n
        
        actual_ts = ticks[actual_index].ts
        realized_latency_ms = (actual_ts - decision_ts).total_seconds() * 1000
        
        return ExecutionSelection(
            latency_mode="ticks",
            decision_tick_index=decision_index,
            requested_execution_tick_index=requested_index,
            requested_execution_ts=None,
            actual_execution_tick_index=actual_index,
            actual_execution_ts=actual_ts,
            tick_clamped=tick_clamped,
            no_future_tick=False,
            configured_latency_ticks=latency_ticks,
            configured_latency_ms=None,
            realized_latency_ms=realized_latency_ms,
            execution_overshoot_ms=None,
        )
    
    else:  # latency_mode == "time"
        # Time-based mode: select first tick at or after requested timestamp
        requested_ts = decision_ts + timedelta(milliseconds=latency_ms)
        
        # Binary search for first tick at or after requested_ts
        actual_index = None
        for i in range(decision_index, n):
            if ticks[i].ts >= requested_ts:
                actual_index = i
                break
        
        if actual_index is None:
            # No future tick exists at or after requested_ts
            return ExecutionSelection(
                latency_mode="time",
                decision_tick_index=decision_index,
                requested_execution_tick_index=None,
                requested_execution_ts=requested_ts,
                actual_execution_tick_index=None,
                actual_execution_ts=None,
                tick_clamped=False,
                no_future_tick=True,
                configured_latency_ticks=None,
                configured_latency_ms=latency_ms,
                realized_latency_ms=None,
                execution_overshoot_ms=None,
            )
        
        actual_ts = ticks[actual_index].ts
        realized_latency_ms = (actual_ts - decision_ts).total_seconds() * 1000
        overshoot_ms = (actual_ts - requested_ts).total_seconds() * 1000
        
        return ExecutionSelection(
            latency_mode="time",
            decision_tick_index=decision_index,
            requested_execution_tick_index=None,
            requested_execution_ts=requested_ts,
            actual_execution_tick_index=actual_index,
            actual_execution_ts=actual_ts,
            tick_clamped=False,
            no_future_tick=False,
            configured_latency_ticks=None,
            configured_latency_ms=latency_ms,
            realized_latency_ms=realized_latency_ms,
            execution_overshoot_ms=overshoot_ms,
        )


def _select_execution_tick_fast(
    ticks: Sequence[ReplayTick],
    tick_timestamps: tuple[datetime, ...],
    decision_index: int,
    *,
    latency_mode: str,
    latency_ticks: int,
    latency_ms: int,
) -> ExecutionSelection:
    """Performance-optimized selector for repeated calls (validation already done).
    
    INTERNAL USE ONLY by ReplayEngine after once-per-round validation.
    Skips full-sequence validation for O(1) tick mode and O(log n) time mode.
    
    CRITICAL: tick_timestamps MUST be precomputed once per round and reused for
    all decisions to avoid O(n²) behavior. Uses bisect directly on precomputed
    timestamps with lo= parameter (NO per-decision slicing or list construction).
    
    SAFETY: Caller MUST ensure ticks already validated (non-empty, UTC-aware, sorted)
    and tick_timestamps extracted once.
    """
    n = len(ticks)
    decision_ts = ticks[decision_index].ts
    
    if latency_mode == "ticks":
        requested_index = decision_index + latency_ticks
        actual_index = min(requested_index, n - 1)
        tick_clamped = requested_index >= n
        actual_ts = ticks[actual_index].ts
        realized_latency_ms = (actual_ts - decision_ts).total_seconds() * 1000
        
        return ExecutionSelection(
            latency_mode="ticks",
            decision_tick_index=decision_index,
            requested_execution_tick_index=requested_index,
            requested_execution_ts=None,
            actual_execution_tick_index=actual_index,
            actual_execution_ts=actual_ts,
            tick_clamped=tick_clamped,
            no_future_tick=False,
            configured_latency_ticks=latency_ticks,
            configured_latency_ms=None,
            realized_latency_ms=realized_latency_ms,
            execution_overshoot_ms=None,
        )
    
    else:  # latency_mode == "time"
        requested_ts = decision_ts + timedelta(milliseconds=latency_ms)
        
        # TRUE O(log n): bisect directly on precomputed timestamps with lo= parameter
        # NO slicing, NO per-decision list construction
        actual_index = bisect.bisect_left(tick_timestamps, requested_ts, lo=decision_index)
        
        if actual_index >= n:
            # No tick at or after requested_ts
            return ExecutionSelection(
                latency_mode="time",
                decision_tick_index=decision_index,
                requested_execution_tick_index=None,
                requested_execution_ts=requested_ts,
                actual_execution_tick_index=None,
                actual_execution_ts=None,
                tick_clamped=False,
                no_future_tick=True,
                configured_latency_ticks=None,
                configured_latency_ms=latency_ms,
                realized_latency_ms=None,
                execution_overshoot_ms=None,
            )
        
        actual_ts = ticks[actual_index].ts
        realized_latency_ms = (actual_ts - decision_ts).total_seconds() * 1000
        overshoot_ms = (actual_ts - requested_ts).total_seconds() * 1000
        
        return ExecutionSelection(
            latency_mode="time",
            decision_tick_index=decision_index,
            requested_execution_tick_index=None,
            requested_execution_ts=requested_ts,
            actual_execution_tick_index=actual_index,
            actual_execution_ts=actual_ts,
            tick_clamped=False,
            no_future_tick=False,
            configured_latency_ticks=None,
            configured_latency_ms=latency_ms,
            realized_latency_ms=realized_latency_ms,
            execution_overshoot_ms=overshoot_ms,
        )


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    """Konfigurasi replay (sizing, fill model, vol, seed)."""

    limits: SizingLimits
    params: StrategyParams
    vol: Decimal
    starting_balance: Decimal
    fee_model: FeeModel = field(default_factory=ProportionalTakerFee)
    latency_mode: str = "ticks"  # "ticks" | "time"
    latency_ticks: int = 1  # tick mode: decision at tick t → fill at tick t+latency
    latency_ms: int = 100  # time mode: execution delay in milliseconds
    competition_fraction: Decimal = _ZERO
    slippage_enabled: bool = True
    seed: int = 42

    @classmethod
    def from_settings(cls, settings: Settings, *, delta_threshold: Decimal) -> ReplayConfig:
        """Bangun konfigurasi replay dari Settings (threshold di-resolve pemanggil)."""
        return cls(
            limits=SizingLimits.from_settings(settings),
            params=StrategyParams.from_settings(settings, delta_threshold=delta_threshold),
            vol=settings.backtest_vol_per_sqrt_sec,
            starting_balance=settings.paper_starting_balance,
            fee_model=ProportionalTakerFee(settings.fee_rate, settings.fee_exponent),
            latency_mode=settings.backtest_latency_mode,
            latency_ticks=settings.backtest_latency_ticks,
            latency_ms=settings.backtest_latency_ms,
            competition_fraction=settings.backtest_competition_fraction,
            seed=settings.backtest_seed,
        )


@dataclass(frozen=True, slots=True)
class RoundDiagnostics:
    """Diagnostik per ronde ter-entry (untuk reliability curve & distribusi)."""

    round_no: int
    p_win_entry: Decimal
    net_edge_entry: Decimal
    won: bool  # sisi yang dimasuki (leader) menang sesuai label Gamma
    pnl: Decimal


# Klasifikasi gagal-fill (Task A — observability murni, bukan keputusan trading).
ROUND_FILLED = "FILLED"  # >= 1 entry fill
ROUND_SIGNAL_NO_FILL = "SIGNAL_NO_FILL"  # >= 1 EnterOrder tapi 0 fill (likuiditas ekor)
ROUND_NO_SIGNAL = "NO_SIGNAL"  # 0 EnterOrder (edge memang tak ada/tipis)

# Entry diagnostics (Task G2 — alasan Strategy NoOp di jalur entry + ENTER).
# String HARUS sama persis dengan reason di domain/strategy._consider_entry.
ENTRY_REASON_TIME_LEFT = "time_left>t_entry"
ENTRY_REASON_DELTA = "abs_delta<threshold"
ENTRY_REASON_ASK_LOW = "ask<min_price"
ENTRY_REASON_ASK_HIGH = "ask>max_price"
ENTRY_REASON_EDGE = "net_edge<min_edge"
ENTRY_REASON_ENTER = "ENTER"  # EnterOrder dipancarkan (lolos semua gerbang)
ENTRY_REASON_KEYS: tuple[str, ...] = (
    ENTRY_REASON_TIME_LEFT,
    ENTRY_REASON_DELTA,
    ENTRY_REASON_ASK_LOW,
    ENTRY_REASON_ASK_HIGH,
    ENTRY_REASON_EDGE,
    ENTRY_REASON_ENTER,
)
_ENTRY_NOOP_REASONS = frozenset(ENTRY_REASON_KEYS) - {ENTRY_REASON_ENTER}


def _new_entry_reasons() -> dict[str, int]:
    return dict.fromkeys(ENTRY_REASON_KEYS, 0)


@dataclass(frozen=True, slots=True)
class RoundObservation:
    """Observasi gagal-fill per ronde (Task A). TIDAK mempengaruhi PnL/keputusan."""

    classification: str  # ROUND_FILLED | ROUND_SIGNAL_NO_FILL | ROUND_NO_SIGNAL
    enter_orders_yielded: int
    fills: int  # entry fill sukses
    fok_rejected_empty_book: int
    # Entry diagnostics (Task G2): hitungan alasan NoOp jalur entry + ENTER.
    entry_reasons: dict[str, int] = field(default_factory=_new_entry_reasons)
    # Fill-failure diagnostics (Task G3): klasifikasi sebab NO_FILL per EnterOrder.
    fill_failures: dict[str, int] = field(default_factory=_new_fill_failures)
    # Sizing diagnostics (Task G4): cap binding + klasifikasi + sampel per EnterOrder.
    sizing_binding: dict[str, int] = field(default_factory=_new_sizing_binding)
    sizing_class: dict[str, int] = field(default_factory=_new_sizing_class)
    sizing_samples: tuple[SizingDiagnostic, ...] = ()
    # Entry timing (exact timestamps for decision-to-fill latency diagnostics; None if no entry).
    entry_decision_ts: datetime | None = None  # tick when EnterOrder emitted
    entry_fill_ts: datetime | None = None  # tick when fill occurred
    # Entry tick indices (exact indices for latency audit; None if no entry).
    entry_decision_tick_index: int | None = None  # tick index when decision occurred
    requested_entry_execution_tick_index: int | None = None  # decision_index + latency_ticks
    actual_entry_execution_tick_index: int | None = None  # min(requested, n-1)
    entry_execution_clamped: bool | None = None  # requested >= n
    # Latency configuration (Sub-Task 2; None if no entry).
    latency_mode: str | None = None  # "ticks" | "time"
    configured_latency_ticks: int | None = None  # tick mode config
    configured_latency_ms: int | None = None  # time mode config
    requested_entry_execution_ts: datetime | None = None  # time mode: decision_ts + latency_ms
    realized_entry_latency_ms: float | None = None  # actual_ts - decision_ts
    entry_execution_overshoot_ms: float | None = None  # time mode: actual_ts - requested_ts
    successful_entry_limit_price: Decimal | None = None  # EnterOrder limit price (when filled)
    # No-future-tick counters (Sub-Task 2; time mode only).
    no_future_tick_entry_attempts: int = 0
    no_future_tick_hedge_attempts: int = 0
    no_future_tick_exit_attempts: int = 0


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    """Ringkasan hasil replay seluruh ronde (input metrik docs/09 §9.4)."""

    rounds_total: int
    rounds_entered: int
    wins: int
    losses: int
    total_pnl: Decimal
    final_balance: Decimal
    results: tuple[RoundResult, ...]
    diagnostics: tuple[RoundDiagnostics, ...] = ()
    # --- fill-failure observability (Task A; default 0 = kompatibel mundur) ---
    rounds_filled: int = 0
    rounds_signal_no_fill: int = 0
    rounds_no_signal: int = 0
    enter_orders_yielded: int = 0
    fills_total: int = 0
    fok_rejected_empty_book: int = 0
    # Entry diagnostics (Task G2): hitungan alasan NoOp jalur entry + ENTER.
    entry_reason_counts: dict[str, int] = field(default_factory=_new_entry_reasons)
    # Fill-failure diagnostics (Task G3): klasifikasi sebab NO_FILL per EnterOrder.
    fill_failure_counts: dict[str, int] = field(default_factory=_new_fill_failures)
    # Sizing diagnostics (Task G4): cap binding, klasifikasi min-order, distribusi.
    sizing_binding_counts: dict[str, int] = field(default_factory=_new_sizing_binding)
    sizing_class_counts: dict[str, int] = field(default_factory=_new_sizing_class)
    sizing_raw_stats: SizingStat = field(default_factory=SizingStat)
    sizing_rounded_stats: SizingStat = field(default_factory=SizingStat)
    sizing_kelly_stats: SizingStat = field(default_factory=SizingStat)
    sizing_notional_stats: SizingStat = field(default_factory=SizingStat)
    sizing_bankroll_stats: SizingStat = field(default_factory=SizingStat)
    sizing_depth_stats: SizingStat = field(default_factory=SizingStat)
    # Depth diagnostics (Task G5): depth mentah + rasio terhadap min_order_size.
    depth_available_stats: SizingStat = field(default_factory=SizingStat)
    depth_ratio_buckets: dict[str, int] = field(default_factory=_new_ratio_buckets)
    raw_ratio_buckets: dict[str, int] = field(default_factory=_new_ratio_buckets)
    # No-future-tick counters (Sub-Task 2; time mode only).
    no_future_tick_entry_attempts: int = 0
    no_future_tick_hedge_attempts: int = 0
    no_future_tick_exit_attempts: int = 0

    @property
    def signal_no_fill_rate(self) -> Decimal:
        """Fraksi ronde ber-sinyal yang GAGAL fill (likuiditas ekor terukur)."""
        denom = self.rounds_filled + self.rounds_signal_no_fill
        return Decimal(self.rounds_signal_no_fill) / Decimal(denom) if denom else _ZERO


# ----- engine -----


class _ObsTally:
    """Akumulator inkremental observasi gagal-fill (Task A; memori ~konstan)."""

    def __init__(self) -> None:
        self.rounds_filled = 0
        self.rounds_signal_no_fill = 0
        self.rounds_no_signal = 0
        self.enter_orders_yielded = 0
        self.fills_total = 0
        self.fok_rejected_empty_book = 0
        self.entry_reasons: dict[str, int] = _new_entry_reasons()
        self.fill_failures: dict[str, int] = _new_fill_failures()
        self.sizing_binding: dict[str, int] = _new_sizing_binding()
        self.sizing_class: dict[str, int] = _new_sizing_class()
        self.raw_stats = _StreamStats()
        self.rounded_stats = _StreamStats()
        self.kelly_stats = _StreamStats()
        self.notional_stats = _StreamStats()
        self.bankroll_stats = _StreamStats()
        self.depth_stats = _StreamStats()
        # --- depth diagnostics (Task G5) ---
        self.depth_available_stats = _StreamStats()
        self.depth_ratio_buckets: dict[str, int] = _new_ratio_buckets()
        self.raw_ratio_buckets: dict[str, int] = _new_ratio_buckets()
        # --- no-future-tick counters (Sub-Task 2) ---
        self.no_future_tick_entry_attempts = 0
        self.no_future_tick_hedge_attempts = 0
        self.no_future_tick_exit_attempts = 0

    def add(self, obs: RoundObservation) -> None:
        """Roll-up satu observasi ronde."""
        if obs.classification == ROUND_FILLED:
            self.rounds_filled += 1
        elif obs.classification == ROUND_SIGNAL_NO_FILL:
            self.rounds_signal_no_fill += 1
        else:
            self.rounds_no_signal += 1
        self.enter_orders_yielded += obs.enter_orders_yielded
        self.fills_total += obs.fills
        self.fok_rejected_empty_book += obs.fok_rejected_empty_book
        for key, count in obs.entry_reasons.items():
            self.entry_reasons[key] = self.entry_reasons.get(key, 0) + count
        for key, count in obs.fill_failures.items():
            self.fill_failures[key] = self.fill_failures.get(key, 0) + count
        for key, count in obs.sizing_binding.items():
            self.sizing_binding[key] = self.sizing_binding.get(key, 0) + count
        for key, count in obs.sizing_class.items():
            self.sizing_class[key] = self.sizing_class.get(key, 0) + count
        for sd in obs.sizing_samples:
            self.raw_stats.add(sd.raw_size)
            self.rounded_stats.add(sd.rounded_size)
            self.kelly_stats.add(sd.size_kelly)
            self.notional_stats.add(sd.cap_notional)
            self.bankroll_stats.add(sd.cap_bankroll)
            self.depth_stats.add(sd.cap_depth)
            # G5: depth mentah + rasio terhadap min_order_size.
            self.depth_available_stats.add(sd.depth_available)
            if sd.min_order_size > _ZERO:
                self.depth_ratio_buckets[_ratio_bucket(sd.depth_available / sd.min_order_size)] += 1
                self.raw_ratio_buckets[_ratio_bucket(sd.raw_size / sd.min_order_size)] += 1
        # Sub-Task 2: aggregate no-future-tick counters
        self.no_future_tick_entry_attempts += obs.no_future_tick_entry_attempts
        self.no_future_tick_hedge_attempts += obs.no_future_tick_hedge_attempts
        self.no_future_tick_exit_attempts += obs.no_future_tick_exit_attempts

    def as_kwargs(self) -> dict[str, int]:
        """Field observability int untuk konstruksi :class:`ReplaySummary`."""
        return {
            "rounds_filled": self.rounds_filled,
            "rounds_signal_no_fill": self.rounds_signal_no_fill,
            "rounds_no_signal": self.rounds_no_signal,
            "enter_orders_yielded": self.enter_orders_yielded,
            "fills_total": self.fills_total,
            "fok_rejected_empty_book": self.fok_rejected_empty_book,
            "no_future_tick_entry_attempts": self.no_future_tick_entry_attempts,
            "no_future_tick_hedge_attempts": self.no_future_tick_hedge_attempts,
            "no_future_tick_exit_attempts": self.no_future_tick_exit_attempts,
        }

    def entry_counts(self) -> dict[str, int]:
        """Hitungan alasan entry (Task G2) sebagai dict baru (stabil)."""
        return dict(self.entry_reasons)

    def fill_failure_counts(self) -> dict[str, int]:
        """Hitungan sebab gagal-fill (Task G3) sebagai dict baru (stabil)."""
        return dict(self.fill_failures)

    def sizing_binding_counts(self) -> dict[str, int]:
        """Hitungan cap binding (Task G4) sebagai dict baru (stabil)."""
        return dict(self.sizing_binding)

    def sizing_class_counts(self) -> dict[str, int]:
        """Hitungan klasifikasi min-order (Task G4) sebagai dict baru (stabil)."""
        return dict(self.sizing_class)

    def depth_ratio_counts(self) -> dict[str, int]:
        """Bucket rasio depth_available/min_order_size (Task G5) sebagai dict baru."""
        return dict(self.depth_ratio_buckets)

    def raw_ratio_counts(self) -> dict[str, int]:
        """Bucket rasio raw_size/min_order_size (Task G5) sebagai dict baru."""
        return dict(self.raw_ratio_buckets)


class _RoundLedger:
    """State akumulasi PnL satu ronde (holdings per token + arus kas)."""

    def __init__(self) -> None:
        self.holdings: dict[str, Decimal] = {}
        self.cash: Decimal = _ZERO  # arus kas bersih ronde (mulai 0)
        self.hedge_cost: Decimal = _ZERO
        self.entry_token: str | None = None
        self.entry_price: Decimal = _ZERO
        self.entry_size: Decimal = _ZERO
        self.entry_outcome: str = "NONE"
        self.entry_p_win: Decimal = _ZERO
        self.entry_net_edge: Decimal = _ZERO
        self.fills: list[Fill] = []
        # --- observability gagal-fill (Task A; tak mempengaruhi PnL) ---
        self.enter_orders_yielded: int = 0
        self.entry_fills: int = 0
        self.fok_rejected_empty_book: int = 0
        # --- klasifikasi sebab gagal-fill (Task G3; observability murni) ---
        self.fill_failures: dict[str, int] = _new_fill_failures()
        # --- sizing diagnostics (Task G4; observability murni, per ronde) ---
        self.sizing_binding: dict[str, int] = _new_sizing_binding()
        self.sizing_class: dict[str, int] = _new_sizing_class()
        self.sizing_samples: list[SizingDiagnostic] = []
        # --- exact entry timing (observability murni; decision + fill timestamps) ---
        self.entry_decision_ts: datetime | None = None  # tick when EnterOrder was emitted
        # --- exact entry tick indices (observability murni; latency audit correctness) ---
        self.entry_decision_tick_index: int | None = None
        self.requested_entry_execution_tick_index: int | None = None
        self.actual_entry_execution_tick_index: int | None = None
        self.entry_execution_clamped: bool | None = None
        # --- Sub-Task 2: latency observability ---
        self.latency_mode: str | None = None
        self.configured_latency_ticks: int | None = None
        self.configured_latency_ms: int | None = None
        self.requested_entry_execution_ts: datetime | None = None
        self.realized_entry_latency_ms: float | None = None
        self.entry_execution_overshoot_ms: float | None = None
        self.successful_entry_limit_price: Decimal | None = None
        # --- no-future-tick counters (Sub-Task 2; time mode only) ---
        self.no_future_tick_entry_attempts: int = 0
        self.no_future_tick_hedge_attempts: int = 0
        self.no_future_tick_exit_attempts: int = 0

    @property
    def entered(self) -> bool:
        return self.entry_token is not None


class ReplayEngine:
    """Harness replay deterministik (docs/08 §8.13).

    Args:
        config: :class:`ReplayConfig` (sizing, strategy, fill model, vol, seed).
    """

    def __init__(self, config: ReplayConfig) -> None:
        self._cfg = config
        self._signal_engine = SignalEngine(fee_model=config.fee_model)
        self._strategy = Strategy(config.params)
        self._rng = random.Random(config.seed)  # reproducibility (bukan kripto)

    # ----- per-round simulation -----

    def run_round(
        self,
        rnd: Round,
        ticks: Sequence[ReplayTick],
        *,
        bankroll: Decimal,
    ) -> RoundResult | None:
        """Simulasikan satu ronde. Kembalikan :class:`RoundResult` bila ada entry.

        Settlement memakai ``rnd.resolved_outcome`` (label Gamma). ``None`` bila
        ronde belum resolved atau tak ada tick.
        """
        detailed = self.simulate(rnd, ticks, bankroll=bankroll)
        return None if detailed is None else detailed[0]

    def simulate(
        self,
        rnd: Round,
        ticks: Sequence[ReplayTick],
        *,
        bankroll: Decimal,
    ) -> tuple[RoundResult, RoundDiagnostics] | None:
        """Inti simulasi satu ronde → (RoundResult, RoundDiagnostics) atau None.

        Pembungkus tipis :meth:`observe` (kompat lama). Lihat :meth:`observe`
        untuk metrik gagal-fill (Task A).
        """
        result, diag, _obs = self.observe(rnd, ticks, bankroll=bankroll)
        if result is None or diag is None:
            return None
        return result, diag

    def observe(
        self,
        rnd: Round,
        ticks: Sequence[ReplayTick],
        *,
        bankroll: Decimal,
    ) -> tuple[RoundResult | None, RoundDiagnostics | None, RoundObservation]:
        """Simulasi satu ronde + observasi gagal-fill (Task A).

        Mengembalikan ``(RoundResult|None, RoundDiagnostics|None, RoundObservation)``.
        ``RoundResult``/``RoundDiagnostics`` ``None`` bila tidak ada entry (sama
        seperti perilaku lama); :class:`RoundObservation` SELALU diisi & mengklasifikasi
        ronde: FILLED / SIGNAL_NO_FILL / NO_SIGNAL (observability murni, tak mengubah
        angka PnL/keputusan).
        """
        if rnd.resolved_outcome is None or not ticks:
            return None, None, RoundObservation(
                ROUND_NO_SIGNAL, 0, 0, 0,
                entry_decision_ts=None,
                entry_fill_ts=None,
                entry_decision_tick_index=None,
                requested_entry_execution_tick_index=None,
                actual_entry_execution_tick_index=None,
                entry_execution_clamped=None,
            )

        # Sub-Task 2 CORRECTION: Validate ticks ONCE and build timestamp sequence ONCE
        # to avoid O(n²). Precomputed tick_timestamps reused for all decisions.
        n = len(ticks)
        if ticks[0].ts.tzinfo is None:
            raise ValueError(f"Round {rnd.round_no}: tick 0 has naive timestamp (must be UTC-aware)")
        for i in range(1, n):
            if ticks[i].ts.tzinfo is None:
                raise ValueError(f"Round {rnd.round_no}: tick {i} has naive timestamp (must be UTC-aware)")
            if ticks[i].ts < ticks[i - 1].ts:
                raise ValueError(f"Round {rnd.round_no}: ticks not sorted at index {i}")
        
        # Build timestamp sequence ONCE for O(log n) bisect (NO per-decision reconstruction)
        tick_timestamps = tuple(t.ts for t in ticks)

        clock = SimClock(ticks[0].ts)
        ledger = _RoundLedger()
        limits = self._round_limits(rnd)
        closed_early = False
        entry_reasons = _new_entry_reasons()

        for i, tick in enumerate(ticks):
            clock.set(tick.ts)
            mbook = MarketBook(up=tick.book_up, down=tick.book_down)
            signal = self._signal_engine.compute(
                rnd,
                tick.btc_price,
                clock.now(),
                self._cfg.vol,
                book_up=tick.book_up,
                book_down=tick.book_down,
            )
            position = self._current_position(rnd, ledger)

            for decision in self._strategy.on_tick(signal, mbook, position):
                if isinstance(decision, EnterOrder):
                    entry_reasons[ENTRY_REASON_ENTER] += 1  # G2: lolos semua gerbang
                    ledger.enter_orders_yielded += 1  # R-A1: sinyal terbentuk
                    # Sub-Task 2 CORRECTION: Pass precomputed tick_timestamps (NO per-decision slice/list)
                    exec_sel = _select_execution_tick_fast(
                        ticks,
                        tick_timestamps,
                        i,
                        latency_mode=self._cfg.latency_mode,
                        latency_ticks=self._cfg.latency_ticks,
                        latency_ms=self._cfg.latency_ms,
                    )
                    if exec_sel.no_future_tick:
                        ledger.no_future_tick_entry_attempts += 1
                        continue  # Skip this attempt; no fill possible
                    self._exec_entry(
                        decision, signal, rnd, mbook, ticks, exec_sel, ledger, limits, bankroll,
                    )
                elif isinstance(decision, NoOp) and decision.reason in _ENTRY_NOOP_REASONS:
                    entry_reasons[decision.reason] += 1  # G2: alasan gerbang entry
                elif isinstance(decision, Hedge):
                    # Sub-Task 2 CORRECTION: Pass precomputed tick_timestamps
                    exec_sel = _select_execution_tick_fast(
                        ticks,
                        tick_timestamps,
                        i,
                        latency_mode=self._cfg.latency_mode,
                        latency_ticks=self._cfg.latency_ticks,
                        latency_ms=self._cfg.latency_ms,
                    )
                    if exec_sel.no_future_tick:
                        ledger.no_future_tick_hedge_attempts += 1
                        continue
                    self._exec_hedge(decision, rnd, ticks, exec_sel, ledger, limits)
                elif isinstance(decision, Exit):
                    # Sub-Task 2 CORRECTION: Pass precomputed tick_timestamps
                    exec_sel = _select_execution_tick_fast(
                        ticks,
                        tick_timestamps,
                        i,
                        latency_mode=self._cfg.latency_mode,
                        latency_ticks=self._cfg.latency_ticks,
                        latency_ms=self._cfg.latency_ms,
                    )
                    if exec_sel.no_future_tick:
                        ledger.no_future_tick_exit_attempts += 1
                        continue
                    if self._exec_exit(decision, rnd, ticks, exec_sel, ledger):
                        closed_early = True
            if closed_early:
                break

        obs = self._classify(ledger, entry_reasons)
        if not ledger.entered:
            return None, None, obs
        result = self._settle(rnd, ledger, bankroll)
        won = ledger.entry_outcome == (
            rnd.resolved_outcome.value if rnd.resolved_outcome is not None else ""
        )
        diag = RoundDiagnostics(
            round_no=rnd.round_no,
            p_win_entry=ledger.entry_p_win,
            net_edge_entry=ledger.entry_net_edge,
            won=won,
            pnl=result.pnl,
        )
        return result, diag, obs

    @staticmethod
    def _classify(ledger: _RoundLedger, entry_reasons: dict[str, int]) -> RoundObservation:
        """Klasifikasikan ronde (R-A3) dari flag lokal ledger + alasan entry (G2)."""
        if ledger.entered:
            classification = ROUND_FILLED
        elif ledger.enter_orders_yielded > 0:
            classification = ROUND_SIGNAL_NO_FILL
        else:
            classification = ROUND_NO_SIGNAL
        # Extract exact entry decision and fill timestamps from ledger.
        # entry_decision_ts: tick when successful EnterOrder was emitted.
        # entry_fill_ts: tick when that order was actually filled (after latency).
        entry_decision_ts = ledger.entry_decision_ts if ledger.entered else None
        entry_fill_ts = ledger.fills[0].ts if ledger.fills else None
        # Extract exact tick indices for latency audit correctness.
        entry_decision_tick_index = ledger.entry_decision_tick_index if ledger.entered else None
        requested_entry_execution_tick_index = ledger.requested_entry_execution_tick_index if ledger.entered else None
        actual_entry_execution_tick_index = ledger.actual_entry_execution_tick_index if ledger.entered else None
        entry_execution_clamped = ledger.entry_execution_clamped if ledger.entered else None
        # Sub-Task 2: Extract latency observability
        latency_mode = ledger.latency_mode if ledger.entered else None
        configured_latency_ticks = ledger.configured_latency_ticks if ledger.entered else None
        configured_latency_ms = ledger.configured_latency_ms if ledger.entered else None
        requested_entry_execution_ts = ledger.requested_entry_execution_ts if ledger.entered else None
        realized_entry_latency_ms = ledger.realized_entry_latency_ms if ledger.entered else None
        entry_execution_overshoot_ms = ledger.entry_execution_overshoot_ms if ledger.entered else None
        successful_entry_limit_price = ledger.successful_entry_limit_price if ledger.entered else None
        return RoundObservation(
            classification=classification,
            enter_orders_yielded=ledger.enter_orders_yielded,
            fills=ledger.entry_fills,
            fok_rejected_empty_book=ledger.fok_rejected_empty_book,
            entry_reasons=entry_reasons,
            fill_failures=ledger.fill_failures,
            sizing_binding=ledger.sizing_binding,
            sizing_class=ledger.sizing_class,
            sizing_samples=tuple(ledger.sizing_samples),
            entry_decision_ts=entry_decision_ts,
            entry_fill_ts=entry_fill_ts,
            entry_decision_tick_index=entry_decision_tick_index,
            requested_entry_execution_tick_index=requested_entry_execution_tick_index,
            actual_entry_execution_tick_index=actual_entry_execution_tick_index,
            entry_execution_clamped=entry_execution_clamped,
            latency_mode=latency_mode,
            configured_latency_ticks=configured_latency_ticks,
            configured_latency_ms=configured_latency_ms,
            requested_entry_execution_ts=requested_entry_execution_ts,
            realized_entry_latency_ms=realized_entry_latency_ms,
            entry_execution_overshoot_ms=entry_execution_overshoot_ms,
            successful_entry_limit_price=successful_entry_limit_price,
            no_future_tick_entry_attempts=ledger.no_future_tick_entry_attempts,
            no_future_tick_hedge_attempts=ledger.no_future_tick_hedge_attempts,
            no_future_tick_exit_attempts=ledger.no_future_tick_exit_attempts,
        )

    def run(
        self,
        rounds: Sequence[tuple[Round, Sequence[ReplayTick]]],
    ) -> ReplaySummary:
        """Jalankan replay untuk banyak ronde; akumulasi equity & ringkasan."""
        balance = self._cfg.starting_balance
        results: list[RoundResult] = []
        diagnostics: list[RoundDiagnostics] = []
        wins = losses = entered = 0
        obs_tally = _ObsTally()
        for rnd, ticks in rounds:
            res, diag, obs = self.observe(rnd, ticks, bankroll=balance)
            obs_tally.add(obs)
            if res is None or diag is None:
                continue
            entered += 1
            balance = res.balance_after
            results.append(res)
            diagnostics.append(diag)
            if res.pnl > _ZERO:
                wins += 1
            elif res.pnl < _ZERO:
                losses += 1
        return ReplaySummary(
            rounds_total=len(rounds),
            rounds_entered=entered,
            wins=wins,
            losses=losses,
            total_pnl=balance - self._cfg.starting_balance,
            final_balance=balance,
            results=tuple(results),
            diagnostics=tuple(diagnostics),
            entry_reason_counts=obs_tally.entry_counts(),
            fill_failure_counts=obs_tally.fill_failure_counts(),
            sizing_binding_counts=obs_tally.sizing_binding_counts(),
            sizing_class_counts=obs_tally.sizing_class_counts(),
            sizing_raw_stats=obs_tally.raw_stats.summary(),
            sizing_rounded_stats=obs_tally.rounded_stats.summary(),
            sizing_kelly_stats=obs_tally.kelly_stats.summary(),
            sizing_notional_stats=obs_tally.notional_stats.summary(),
            sizing_bankroll_stats=obs_tally.bankroll_stats.summary(),
            sizing_depth_stats=obs_tally.depth_stats.summary(),
            depth_available_stats=obs_tally.depth_available_stats.summary(),
            depth_ratio_buckets=obs_tally.depth_ratio_counts(),
            raw_ratio_buckets=obs_tally.raw_ratio_counts(),
            **obs_tally.as_kwargs(),
        )

    # ----- helpers -----

    def _round_limits(self, rnd: Round) -> SizingLimits:
        """Limits sizing dengan tick/min_order spesifik ronde."""
        base = self._cfg.limits
        return SizingLimits(
            kelly_fraction=base.kelly_fraction,
            max_notional_round=base.max_notional_round,
            max_bankroll_fraction=base.max_bankroll_fraction,
            fill_safety=base.fill_safety,
            min_edge=base.min_edge,
            max_price=base.max_price,
            min_order_size=rnd.min_order_size,
            tick_size=rnd.tick_size,
        )

    def _current_position(self, rnd: Round, ledger: _RoundLedger) -> Position | None:
        """Posisi sisi yang dimasuki (untuk Strategy.on_tick)."""
        if ledger.entry_token is None:
            return None
        held = ledger.holdings.get(ledger.entry_token, _ZERO)
        if held <= _ZERO:
            return None
        return Position(
            round_no=rnd.round_no,
            token_id=ledger.entry_token,
            size=held,
            avg_price=ledger.entry_price,
        )

    def _fee(self, price: Decimal, qty: Decimal) -> Decimal:
        return self._cfg.fee_model.fee_per_share(price) * qty

    def _exec_entry(  # noqa: PLR0913
        self,
        decision: EnterOrder,
        signal: Signal,
        rnd: Round,
        mbook: MarketBook,
        ticks: Sequence[ReplayTick],
        exec_sel: ExecutionSelection,
        ledger: _RoundLedger,
        limits: SizingLimits,
        bankroll: Decimal,
    ) -> None:
        leader = Outcome(decision.outcome)
        decision_book = mbook.for_outcome(leader)
        depth = sum((lvl.size for lvl in decision_book.asks), _ZERO)
        sized = size(signal, bankroll, depth, limits)
        # G4: cermin observability sizing (read-only; tak mengubah `sized`/keputusan).
        sd = diagnose_size(signal, bankroll, depth, limits)
        ledger.sizing_samples.append(sd)
        ledger.sizing_binding[sd.binding_label] = ledger.sizing_binding.get(sd.binding_label, 0) + 1
        ledger.sizing_class[sd.classification] += 1
        if sized <= _ZERO:
            ledger.fill_failures[FILL_FAIL_REQUESTED_SIZE_ZERO] += 1  # G3
            return
        
        # Sub-Task 2: Get execution tick from selector result
        assert exec_sel.actual_execution_tick_index is not None, "no_future_tick should be handled before _exec_entry"
        exec_tick = ticks[exec_sel.actual_execution_tick_index]
        
        exec_book = exec_tick.book_up if leader is Outcome.UP else exec_tick.book_down
        fr = simulate_fill(
            book=exec_book,
            side=SIDE_BUY,
            limit_price=decision.price,
            requested_size=sized,
            order_type=decision.order_type,
            competition_fraction=self._cfg.competition_fraction,
            ignore_depth=not self._cfg.slippage_enabled,
        )
        if not fr.filled:
            # G3: klasifikasikan sebab gagal-fill (read-only, cermin simulate_fill).
            reason = classify_no_fill(
                book=exec_book,
                side=SIDE_BUY,
                limit_price=decision.price,
                requested_size=sized,
                order_type=decision.order_type,
                competition_fraction=self._cfg.competition_fraction,
                ignore_depth=not self._cfg.slippage_enabled,
            )
            ledger.fill_failures[reason] += 1
            # R-A2: FOK gagal karena ASK sisi pemimpin kosong/tak cukup di exec_tick.
            # "Book pemimpin kosong" = depth ASK exec <= 0 (definisi sama dgn fill 0).
            exec_ask_depth = sum((lvl.size for lvl in exec_book.asks), _ZERO)
            if exec_ask_depth <= _ZERO:
                ledger.fok_rejected_empty_book += 1
            return
        ledger.entry_fills += 1  # R-A4: fills_total (entry sukses)
        # Record exact decision timestamp (observability only; tick when EnterOrder emitted).
        decision_tick = ticks[exec_sel.decision_tick_index]
        ledger.entry_decision_ts = decision_tick.ts
        # Record exact tick indices (observability only; latency audit correctness).
        ledger.entry_decision_tick_index = exec_sel.decision_tick_index
        ledger.requested_entry_execution_tick_index = exec_sel.requested_execution_tick_index
        ledger.actual_entry_execution_tick_index = exec_sel.actual_execution_tick_index
        ledger.entry_execution_clamped = exec_sel.tick_clamped
        # Sub-Task 2: Record latency observability
        ledger.latency_mode = exec_sel.latency_mode
        ledger.configured_latency_ticks = exec_sel.configured_latency_ticks
        ledger.configured_latency_ms = exec_sel.configured_latency_ms
        ledger.requested_entry_execution_ts = exec_sel.requested_execution_ts
        ledger.realized_entry_latency_ms = exec_sel.realized_latency_ms
        ledger.entry_execution_overshoot_ms = exec_sel.execution_overshoot_ms
        ledger.successful_entry_limit_price = decision.price  # Capture EnterOrder limit price exactly
        fee = self._fee(fr.avg_price, fr.filled_size)
        ledger.cash -= fr.notional + fee
        ledger.holdings[decision.token_id] = (
            ledger.holdings.get(decision.token_id, _ZERO) + fr.filled_size
        )
        ledger.entry_token = decision.token_id
        ledger.entry_price = fr.avg_price
        ledger.entry_size = fr.filled_size
        ledger.entry_outcome = decision.outcome
        ledger.entry_p_win = signal.p_win
        ledger.entry_net_edge = signal.net_edge
        ledger.fills.append(
            Fill(
                order_id=f"bt-{rnd.round_no}-entry",
                token_id=decision.token_id,
                price=fr.avg_price,
                size=fr.filled_size,
                ts=exec_tick.ts,
            )
        )

    def _exec_hedge(
        self,
        decision: Hedge,
        rnd: Round,
        ticks: Sequence[ReplayTick],
        exec_sel: ExecutionSelection,
        ledger: _RoundLedger,
        limits: SizingLimits,
    ) -> None:
        assert exec_sel.actual_execution_tick_index is not None, "no_future_tick should be handled before _exec_hedge"
        exec_tick = ticks[exec_sel.actual_execution_tick_index]
        
        opp = Outcome(decision.outcome)
        exec_book = exec_tick.book_up if opp is Outcome.UP else exec_tick.book_down
        pos_size = ledger.holdings.get(ledger.entry_token or "", _ZERO)
        raw = pos_size * decision.hedge_fraction
        depth_cap = sum((lvl.size for lvl in exec_book.asks), _ZERO) * limits.fill_safety
        hedge_size = round_to_tick(min(raw, depth_cap), limits.tick_size)
        if hedge_size < limits.min_order_size or hedge_size <= _ZERO:
            return
        fr = simulate_fill(
            book=exec_book,
            side=SIDE_BUY,
            limit_price=decision.price,
            requested_size=hedge_size,
            order_type=decision.order_type,
            competition_fraction=self._cfg.competition_fraction,
            ignore_depth=not self._cfg.slippage_enabled,
        )
        if not fr.filled:
            return
        fee = self._fee(fr.avg_price, fr.filled_size)
        cost = fr.notional + fee
        ledger.cash -= cost
        ledger.hedge_cost += cost
        ledger.holdings[decision.token_id] = (
            ledger.holdings.get(decision.token_id, _ZERO) + fr.filled_size
        )
        ledger.fills.append(
            Fill(
                order_id=f"bt-{rnd.round_no}-hedge",
                token_id=decision.token_id,
                price=fr.avg_price,
                size=fr.filled_size,
                ts=exec_tick.ts,
            )
        )

    def _exec_exit(
        self,
        decision: Exit,
        rnd: Round,
        ticks: Sequence[ReplayTick],
        exec_sel: ExecutionSelection,
        ledger: _RoundLedger,
    ) -> bool:
        assert exec_sel.actual_execution_tick_index is not None, "no_future_tick should be handled before _exec_exit"
        exec_tick = ticks[exec_sel.actual_execution_tick_index]
        
        held = Outcome(decision.outcome)
        exec_book = exec_tick.book_up if held is Outcome.UP else exec_tick.book_down
        qty = ledger.holdings.get(decision.token_id, _ZERO)
        if qty <= _ZERO:
            return False
        fr = simulate_fill(
            book=exec_book,
            side="SELL",
            limit_price=decision.price,
            requested_size=qty,
            order_type=decision.order_type,
            competition_fraction=self._cfg.competition_fraction,
            ignore_depth=not self._cfg.slippage_enabled,
        )
        if not fr.filled:
            return False
        fee = self._fee(fr.avg_price, fr.filled_size)
        ledger.cash += fr.notional - fee
        ledger.holdings[decision.token_id] = qty - fr.filled_size
        ledger.fills.append(
            Fill(
                order_id=f"bt-{rnd.round_no}-exit",
                token_id=decision.token_id,
                price=fr.avg_price,
                size=fr.filled_size,
                ts=exec_tick.ts,
            )
        )
        # Exit penuh = ronde ditutup lebih awal.
        return ledger.holdings[decision.token_id] <= _EPS

    def _settle(self, rnd: Round, ledger: _RoundLedger, bankroll: Decimal) -> RoundResult:
        winner_token = rnd.token_id_up if rnd.resolved_outcome is Outcome.UP else rnd.token_id_down
        payout = _ZERO
        for token, qty in ledger.holdings.items():
            if qty <= _ZERO:
                continue
            if token == winner_token:
                payout += qty * _ONE
        cash = ledger.cash + payout
        return RoundResult(
            round_no=rnd.round_no,
            side_taken=ledger.entry_outcome,
            entry_price=ledger.entry_price,
            size=ledger.entry_size,
            hedge_cost=ledger.hedge_cost,
            settled=payout,
            pnl=cash,
            balance_after=bankroll + cash,
        )


# ----- reconstruction dari store -----


def _book_from_snapshot(snap: BookSnapshot) -> OrderBook:
    """Rekonstruksi OrderBook (1 level sintetik dari best + depth agregat).

    Recorder hanya menyimpan best_bid/best_ask + depth agregat (docs/07 §7.3.1),
    jadi book direkonstruksi sebagai SATU level di best dengan size = depth.
    """
    bids = [BookLevel(snap.best_bid, snap.bid_depth or _ZERO)] if snap.best_bid is not None else []
    asks = [BookLevel(snap.best_ask, snap.ask_depth or _ZERO)] if snap.best_ask is not None else []
    return OrderBook(token_id=snap.token_id, ts=snap.ts, bids=bids, asks=asks)


def _price_at(signals: Sequence[Signal], ts: datetime, fallback: Decimal) -> Decimal:
    """Harga BTC (last-value-carried-forward) pada/atau sebelum ``ts``."""
    price = fallback
    for sig in signals:
        if sig.ts <= ts:
            price = sig.price_now
        else:
            break
    return price


def reconstruct_ticks(
    rnd: Round,
    snapshots: Sequence[BookSnapshot],
    signals: Sequence[Signal],
) -> list[ReplayTick]:
    """Bangun urutan :class:`ReplayTick` dari data terekam (LVCF per token).

    Tiap snapshot non-gap memutakhirkan book token-nya; harga BTC diambil LVCF
    dari ``signals`` (fallback ``start_price``). Tick di-emit per event book.
    """
    empty_up = OrderBook(token_id=rnd.token_id_up, ts=rnd.window_start, bids=[], asks=[])
    empty_down = OrderBook(token_id=rnd.token_id_down, ts=rnd.window_start, bids=[], asks=[])
    cur_up = empty_up
    cur_down = empty_down
    ticks: list[ReplayTick] = []
    ordered = sorted((s for s in snapshots if not s.gap), key=lambda s: s.ts)
    for snap in ordered:
        book = _book_from_snapshot(snap)
        if snap.token_id == rnd.token_id_up:
            cur_up = book
        elif snap.token_id == rnd.token_id_down:
            cur_down = book
        else:
            continue  # token asing → abaikan
        price = _price_at(signals, snap.ts, rnd.start_price)
        ticks.append(ReplayTick(ts=snap.ts, btc_price=price, book_up=cur_up, book_down=cur_down))
    return ticks


async def load_round_replays(
    store: Store,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> AsyncIterator[tuple[Round, list[ReplayTick]]]:
    """Stream ronde resolved + tick replay-nya dari :class:`Store` (memori ~konstan).

    **Async generator** (Bug B7): muat metadata ronde sekali (ringan, tanpa book),
    lalu PER RONDE muat ``book_snapshots`` + ``signals`` → ``reconstruct_ticks`` →
    ``yield`` → ticks ronde itu boleh di-GC sebelum ronde berikutnya. Filter
    ``since``/``until``/``limit`` di-pushdown ke SQL (lihat
    :meth:`Store.get_resolved_rounds`), bukan di Python.

    Hanya ronde berlabel Gamma dengan minimal satu tick yang di-yield.
    """
    rounds = await store.get_resolved_rounds(since=since, until=until, limit=limit)
    for rnd in rounds:
        snaps = await store.get_book_snapshots(rnd.round_no)
        sigs = await store.get_signals(rnd.round_no)
        ticks = reconstruct_ticks(rnd, snaps, sigs)
        if ticks:
            yield rnd, ticks


class RunAccumulator:
    """Akumulator statistik replay **streaming** untuk SATU config (memori ~konstan).

    Diberi makan satu ronde via :meth:`feed`; ticks tidak disimpan (boleh di-GC
    setelah feed). Hasil per-ronde (``RoundResult``/``RoundDiagnostics``) ringan
    dikumpulkan untuk :meth:`summary`. Matematika & threading saldo IDENTIK dengan
    :meth:`ReplayEngine.run` (urutan ronde sama → hasil sama).
    """

    def __init__(self, config: ReplayConfig) -> None:
        self._engine = ReplayEngine(config)
        self._start = config.starting_balance
        self._balance = config.starting_balance
        self._results: list[RoundResult] = []
        self._diags: list[RoundDiagnostics] = []
        self._wins = 0
        self._losses = 0
        self._entered = 0
        self._total = 0
        self._obs = _ObsTally()

    def feed(self, rnd: Round, ticks: Sequence[ReplayTick]) -> RoundResult | None:
        """Proses satu ronde; akumulasi statistik. Kembalikan RoundResult bila entry."""
        self._total += 1
        res, diag, obs = self._engine.observe(rnd, ticks, bankroll=self._balance)
        self._obs.add(obs)
        if res is None or diag is None:
            return None
        self._entered += 1
        self._balance = res.balance_after
        self._results.append(res)
        self._diags.append(diag)
        if res.pnl > _ZERO:
            self._wins += 1
        elif res.pnl < _ZERO:
            self._losses += 1
        return res

    def summary(self) -> ReplaySummary:
        """Bangun :class:`ReplaySummary` dari statistik terakumulasi."""
        return ReplaySummary(
            rounds_total=self._total,
            rounds_entered=self._entered,
            wins=self._wins,
            losses=self._losses,
            total_pnl=self._balance - self._start,
            final_balance=self._balance,
            results=tuple(self._results),
            diagnostics=tuple(self._diags),
            entry_reason_counts=self._obs.entry_counts(),
            fill_failure_counts=self._obs.fill_failure_counts(),
            sizing_binding_counts=self._obs.sizing_binding_counts(),
            sizing_class_counts=self._obs.sizing_class_counts(),
            sizing_raw_stats=self._obs.raw_stats.summary(),
            sizing_rounded_stats=self._obs.rounded_stats.summary(),
            sizing_kelly_stats=self._obs.kelly_stats.summary(),
            sizing_notional_stats=self._obs.notional_stats.summary(),
            sizing_bankroll_stats=self._obs.bankroll_stats.summary(),
            sizing_depth_stats=self._obs.depth_stats.summary(),
            depth_available_stats=self._obs.depth_available_stats.summary(),
            depth_ratio_buckets=self._obs.depth_ratio_counts(),
            raw_ratio_buckets=self._obs.raw_ratio_counts(),
            **self._obs.as_kwargs(),
        )


async def stream_accumulators(
    accumulators: Sequence[RunAccumulator],
    rounds: AsyncIterator[tuple[Round, list[ReplayTick]]],
    *,
    on_progress: Callable[[int], None] | None = None,
) -> int:
    """Feed setiap ronde stream ke SEMUA akumulator dalam SATU pass. Return jumlah ronde.

    Memungkinkan ablation/grid multi-config tanpa memuat ulang DB (ticks tiap
    ronde diproses semua akumulator lalu di-GC).
    """
    processed = 0
    async for rnd, ticks in rounds:
        processed += 1
        for acc in accumulators:
            acc.feed(rnd, ticks)
        if on_progress is not None:
            on_progress(processed)
    return processed


async def run_and_persist(
    store: Store,
    config: ReplayConfig,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> ReplaySummary:
    """Stream data terekam, jalankan replay, tulis ``round_results`` & ``equity_curve``.

    Menulis dengan ``mode='backtest'`` per ronde (streaming, memori ~konstan).
    """
    acc = RunAccumulator(config)
    async for rnd, ticks in load_round_replays(store, since=since, until=until, limit=limit):
        res = acc.feed(rnd, ticks)
        if res is not None:
            await store.insert_round_result(res, mode="backtest")
            await store.insert_equity_point(rnd.window_end, res.balance_after, "backtest")
    return acc.summary()
