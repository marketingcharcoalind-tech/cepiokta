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

import random
from dataclasses import dataclass, field
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
    Strategy,
    StrategyParams,
)
from btcbot.exec.sizing import SizingLimits, round_to_tick, size

if TYPE_CHECKING:
    from collections.abc import Sequence
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


def simulate_fill(  # noqa: PLR0913 - parameter eksplisit (keyword-only)
    *,
    book: OrderBook,
    side: str,
    limit_price: Decimal,
    requested_size: Decimal,
    order_type: str,
    competition_fraction: Decimal = _ZERO,
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
    """
    if requested_size <= _ZERO:
        return _NO_FILL
    factor = _ONE - competition_fraction
    is_buy = side == SIDE_BUY
    if is_buy:
        levels = sorted(book.asks, key=lambda lvl: lvl.price)
    else:
        levels = sorted(book.bids, key=lambda lvl: lvl.price, reverse=True)

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


# ----- replay inputs & config -----


@dataclass(frozen=True, slots=True)
class ReplayTick:
    """Satu tick replay: harga BTC + order book UP/DOWN pada ``ts``."""

    ts: datetime
    btc_price: Decimal
    book_up: OrderBook
    book_down: OrderBook


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    """Konfigurasi replay (sizing, fill model, vol, seed)."""

    limits: SizingLimits
    params: StrategyParams
    vol: Decimal
    starting_balance: Decimal
    fee_model: FeeModel = field(default_factory=ProportionalTakerFee)
    latency_ticks: int = 1
    competition_fraction: Decimal = _ZERO
    seed: int = 42

    @classmethod
    def from_settings(cls, settings: Settings, *, delta_threshold: Decimal) -> ReplayConfig:
        """Bangun konfigurasi replay dari Settings (threshold di-resolve pemanggil)."""
        return cls(
            limits=SizingLimits.from_settings(settings),
            params=StrategyParams.from_settings(settings, delta_threshold=delta_threshold),
            vol=settings.backtest_vol_per_sqrt_sec,
            starting_balance=settings.paper_starting_balance,
            fee_model=ProportionalTakerFee(settings.fee_rate),
            latency_ticks=settings.backtest_latency_ticks,
            competition_fraction=settings.backtest_competition_fraction,
            seed=settings.backtest_seed,
        )


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


# ----- engine -----


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
        self.fills: list[Fill] = []

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
        if rnd.resolved_outcome is None or not ticks:
            return None

        clock = SimClock(ticks[0].ts)
        ledger = _RoundLedger()
        limits = self._round_limits(rnd)
        n = len(ticks)
        closed_early = False

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
            exec_tick = ticks[min(i + self._cfg.latency_ticks, n - 1)]

            for decision in self._strategy.on_tick(signal, mbook, position):
                if isinstance(decision, EnterOrder):
                    self._exec_entry(
                        decision, signal, rnd, mbook, exec_tick, ledger, limits, bankroll
                    )
                elif isinstance(decision, Hedge):
                    self._exec_hedge(decision, rnd, exec_tick, ledger, limits)
                elif isinstance(decision, Exit) and self._exec_exit(
                    decision, rnd, exec_tick, ledger
                ):
                    closed_early = True
            if closed_early:
                break

        if not ledger.entered:
            return None
        return self._settle(rnd, ledger, bankroll)

    def run(
        self,
        rounds: Sequence[tuple[Round, Sequence[ReplayTick]]],
    ) -> ReplaySummary:
        """Jalankan replay untuk banyak ronde; akumulasi equity & ringkasan."""
        balance = self._cfg.starting_balance
        results: list[RoundResult] = []
        wins = losses = entered = 0
        for rnd, ticks in rounds:
            res = self.run_round(rnd, ticks, bankroll=balance)
            if res is None:
                continue
            entered += 1
            balance = res.balance_after
            results.append(res)
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
        exec_tick: ReplayTick,
        ledger: _RoundLedger,
        limits: SizingLimits,
        bankroll: Decimal,
    ) -> None:
        leader = Outcome(decision.outcome)
        decision_book = mbook.for_outcome(leader)
        depth = sum((lvl.size for lvl in decision_book.asks), _ZERO)
        sized = size(signal, bankroll, depth, limits)
        if sized <= _ZERO:
            return
        exec_book = exec_tick.book_up if leader is Outcome.UP else exec_tick.book_down
        fr = simulate_fill(
            book=exec_book,
            side=SIDE_BUY,
            limit_price=decision.price,
            requested_size=sized,
            order_type=decision.order_type,
            competition_fraction=self._cfg.competition_fraction,
        )
        if not fr.filled:
            return
        fee = self._fee(fr.avg_price, fr.filled_size)
        ledger.cash -= fr.notional + fee
        ledger.holdings[decision.token_id] = (
            ledger.holdings.get(decision.token_id, _ZERO) + fr.filled_size
        )
        ledger.entry_token = decision.token_id
        ledger.entry_price = fr.avg_price
        ledger.entry_size = fr.filled_size
        ledger.entry_outcome = decision.outcome
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
        exec_tick: ReplayTick,
        ledger: _RoundLedger,
        limits: SizingLimits,
    ) -> None:
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
        exec_tick: ReplayTick,
        ledger: _RoundLedger,
    ) -> bool:
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
    limit: int | None = None,
) -> list[tuple[Round, list[ReplayTick]]]:
    """Muat ronde resolved + tick replay-nya dari :class:`Store`.

    Hanya ronde berlabel Gamma (``resolved_outcome`` terisi) dengan minimal satu
    tick yang disertakan. Ronde tanpa data book di-skip.
    """
    rounds = await store.get_resolved_rounds(limit=limit)
    out: list[tuple[Round, list[ReplayTick]]] = []
    for rnd in rounds:
        snaps = await store.get_book_snapshots(rnd.round_no)
        sigs = await store.get_signals(rnd.round_no)
        ticks = reconstruct_ticks(rnd, snaps, sigs)
        if ticks:
            out.append((rnd, ticks))
    return out


async def run_and_persist(
    store: Store,
    config: ReplayConfig,
    *,
    limit: int | None = None,
) -> ReplaySummary:
    """Muat data terekam, jalankan replay, tulis ``round_results`` & ``equity_curve``.

    Menulis dengan ``mode='backtest'``. Mengembalikan :class:`ReplaySummary`.
    """
    engine = ReplayEngine(config)
    rounds = await load_round_replays(store, limit=limit)
    summary = engine.run(rounds)
    for res in summary.results:
        await store.insert_round_result(res, mode="backtest")
    # equity_curve: satu titik per ronde ter-entry (urut waktu round_no).
    balance = config.starting_balance
    rnd_by_no = {rnd.round_no: rnd for rnd, _ in rounds}
    for res in summary.results:
        balance = res.balance_after
        rnd = rnd_by_no.get(res.round_no)
        ts = rnd.window_end if rnd is not None else None
        if ts is not None:
            await store.insert_equity_point(ts, balance, "backtest")
    return summary
