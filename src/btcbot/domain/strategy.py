"""domain/strategy.py — Strategy ("hedging" + entry) (docs/05 §5.4-5.7, docs/08 §8.8).

Keputusan entry/hedge/exit sebagai domain **murni & deterministik** (tanpa I/O,
tanpa sizing — ukuran dihitung terpisah di ``exec/sizing.py``).

``Strategy.on_tick(signal, book, position) -> list[Decision]`` dengan
``Decision = EnterOrder | Hedge | Exit | NoOp``.

Aturan ENTRY (docs/05 §5.4) — semua filter harus lolos:
- ``time_left <= T_ENTRY_SEC``
- ``|Δ| >= DELTA_THRESHOLD``
- ``MIN_PRICE <= ask <= MAX_PRICE`` (``ask`` = ``signal.ask_win`` sisi pemimpin)
- ``net_edge >= MIN_EDGE`` (``net_edge`` SUDAH net-of-fee ~7% dari SignalEngine →
  PROMPT_GUIDE ✅ VERIFIED REALITY #3; set ``MIN_EDGE > 0`` agar lolos biaya nyata)
- Hanya beli sisi **pemimpin** (taker). NEVER-FADE & NEVER beli ``> MAX_PRICE``.

Aturan KELOLA POSISI (docs/05 §5.5-5.6):
- ``p_win_held < P_EXIT`` ATAU ``flip >= FLIP_RATIO`` → hedge/exit.
  ``flip = depth(opposite) / (depth(held) + depth(opposite))``.
- Prioritas: **Hedge** (beli sisi lawan, micro-hedge) bila ada likuiditas &
  ``ask_opp <= MAX_PRICE``; jika tidak → **Exit** (jual sisi held bila ada bid);
  jika tidak ada keduanya → NoOp (tak bisa bertindak).
- Default (trigger tak aktif) = HOLD sampai resolve (NoOp).

Anti-pattern (docs/05 §5.7) — DILARANG: entry tanpa cek net_edge, beli
``> MAX_PRICE``, fade (melawan pemimpin), menahan posisi kalah tanpa hedge.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from btcbot.domain.models import Outcome
from btcbot.domain.signal import best_ask

if TYPE_CHECKING:
    from btcbot.config.settings import Settings
    from btcbot.domain.models import OrderBook, Position, Signal

_ZERO = Decimal("0")
_ONE = Decimal("1")

# Tipe order taker (docs/04 §4.5).
ORDER_FOK = "FOK"  # fill-or-kill: entry ekor-window (semua atau batal)
ORDER_FAK = "FAK"  # fill-and-kill: hedge/exit (partial fill diperbolehkan)
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"


# ----- Decision types -----


@dataclass(frozen=True, slots=True)
class EnterOrder:
    """Usulan entry taker pada sisi pemimpin (BUY)."""

    token_id: str
    outcome: str  # "UP" | "DOWN" (sisi pemimpin)
    price: Decimal  # = ask_win (harga limit FOK/FAK)
    order_type: str = ORDER_FOK
    side: str = SIDE_BUY
    reason: str = "entry"


@dataclass(frozen=True, slots=True)
class Hedge:
    """Usulan micro-hedge: beli sebagian sisi lawan untuk lock loss kecil."""

    token_id: str
    outcome: str  # sisi LAWAN dari posisi yang dipegang
    price: Decimal  # ask terbaik sisi lawan
    hedge_fraction: Decimal  # fraksi ukuran posisi (sizer yang menerapkan)
    order_type: str = ORDER_FAK
    side: str = SIDE_BUY
    reason: str = "hedge"


@dataclass(frozen=True, slots=True)
class Exit:
    """Usulan exit: jual posisi sisi held (bila ada bid layak)."""

    token_id: str
    outcome: str  # sisi yang dipegang (dijual)
    price: Decimal  # bid terbaik sisi held
    order_type: str = ORDER_FAK
    side: str = SIDE_SELL
    reason: str = "exit"


@dataclass(frozen=True, slots=True)
class NoOp:
    """Tidak ada aksi (dengan alasan untuk audit/debug)."""

    reason: str = "no_op"


Decision = EnterOrder | Hedge | Exit | NoOp


# ----- book container & helpers -----


@dataclass(frozen=True, slots=True)
class MarketBook:
    """Pasangan order book token UP & DOWN untuk satu ronde."""

    up: OrderBook
    down: OrderBook

    def for_outcome(self, outcome: Outcome) -> OrderBook:
        """Kembalikan order book sesuai sisi ``outcome``."""
        return self.up if outcome is Outcome.UP else self.down


def _opposite(outcome: Outcome) -> Outcome:
    return Outcome.DOWN if outcome is Outcome.UP else Outcome.UP


def best_bid(book: OrderBook | None) -> Decimal | None:
    """Kembalikan harga bid terbaik (tertinggi) dari ``book``; None bila kosong."""
    if book is None or not book.bids:
        return None
    return max(level.price for level in book.bids)


def book_depth(book: OrderBook | None) -> Decimal:
    """Total likuiditas (Σ size bids + asks) sebuah token sebagai proxy 'depth'."""
    if book is None:
        return _ZERO
    return sum((lvl.size for lvl in (*book.bids, *book.asks)), _ZERO)


def flip_ratio(held_book: OrderBook | None, opp_book: OrderBook | None) -> Decimal:
    """``depth(opposite) / (depth(held) + depth(opposite))``; 0 bila tak ada depth."""
    d_held = book_depth(held_book)
    d_opp = book_depth(opp_book)
    total = d_held + d_opp
    if total <= _ZERO:
        return _ZERO
    return d_opp / total


# ----- params -----


@dataclass(frozen=True, slots=True)
class StrategyParams:
    """Parameter keputusan strategi (docs/05 §5.2)."""

    t_entry_sec: int
    delta_threshold: Decimal
    min_price: Decimal
    max_price: Decimal
    min_edge: Decimal
    flip_ratio: Decimal
    hedge_fraction: Decimal
    p_exit: Decimal

    def __post_init__(self) -> None:
        if self.min_price > self.max_price:
            raise ValueError(f"min_price ({self.min_price}) > max_price ({self.max_price})")
        if self.delta_threshold < _ZERO:
            raise ValueError(f"delta_threshold tidak boleh negatif: {self.delta_threshold}")
        if self.t_entry_sec <= 0:
            raise ValueError(f"t_entry_sec harus > 0: {self.t_entry_sec}")

    @classmethod
    def from_settings(cls, settings: Settings, *, delta_threshold: Decimal) -> StrategyParams:
        """Bangun dari Settings. ``delta_threshold`` di-resolve pemanggil.

        ``DELTA_THRESHOLD='auto'`` skala dengan volatilitas → harus dihitung di
        lapisan app (butuh ``vol``) lalu disuntikkan sebagai Decimal di sini.
        """
        return cls(
            t_entry_sec=settings.t_entry_sec,
            delta_threshold=delta_threshold,
            min_price=settings.min_price,
            max_price=settings.max_price,
            min_edge=settings.min_edge,
            flip_ratio=settings.flip_ratio,
            hedge_fraction=settings.hedge_fraction,
            p_exit=settings.p_exit,
        )


# ----- strategy -----


class Strategy:
    """Mesin keputusan entry/hedge/exit (murni). Parameter di-inject.

    Args:
        params: :class:`StrategyParams` (dari Settings + threshold ter-resolve).
    """

    def __init__(self, params: StrategyParams) -> None:
        self._p = params

    def on_tick(
        self,
        signal: Signal,
        book: MarketBook,
        position: Position | None,
    ) -> list[Decision]:
        """Kembalikan daftar keputusan untuk satu tick (selalu >= 1 item).

        Tanpa posisi → pertimbangkan ENTRY. Dengan posisi → KELOLA (hedge/exit/hold).
        """
        if position is None or position.size <= _ZERO:
            return [self._consider_entry(signal, book)]
        return [self._manage_position(signal, book, position)]

    def _consider_entry(self, signal: Signal, book: MarketBook) -> Decision:
        p = self._p
        if signal.time_left_sec > p.t_entry_sec:
            return NoOp(reason="time_left>t_entry")
        if abs(signal.delta) < p.delta_threshold:
            return NoOp(reason="abs_delta<threshold")
        ask = signal.ask_win
        if ask < p.min_price:
            return NoOp(reason="ask<min_price")
        if ask > p.max_price:  # NEVER beli > MAX_PRICE (anti chase)
            return NoOp(reason="ask>max_price")
        if signal.net_edge < p.min_edge:
            return NoOp(reason="net_edge<min_edge")

        leader = Outcome(signal.leader)  # never-fade: hanya sisi pemimpin
        leader_book = book.for_outcome(leader)
        return EnterOrder(
            token_id=leader_book.token_id,
            outcome=leader.value,
            price=ask,
            order_type=ORDER_FOK,
            reason="entry",
        )

    def _held_outcome(self, book: MarketBook, position: Position) -> Outcome | None:
        if position.token_id == book.up.token_id:
            return Outcome.UP
        if position.token_id == book.down.token_id:
            return Outcome.DOWN
        return None

    def _manage_position(self, signal: Signal, book: MarketBook, position: Position) -> Decision:
        p = self._p
        held = self._held_outcome(book, position)
        if held is None:
            return NoOp(reason="position_token_not_in_book")

        leader = Outcome(signal.leader)
        # p_win sinyal = prob sisi pemimpin; konversi ke prob sisi yang dipegang.
        p_win_held = signal.p_win if held is leader else (_ONE - signal.p_win)

        held_book = book.for_outcome(held)
        opp = _opposite(held)
        opp_book = book.for_outcome(opp)
        flip = flip_ratio(held_book, opp_book)

        trigger_exit = p_win_held < p.p_exit
        trigger_flip = flip >= p.flip_ratio
        if not (trigger_exit or trigger_flip):
            return NoOp(reason="hold")

        reason_suffix = "p_win<p_exit" if trigger_exit else "book_flip"

        # Prioritas A: micro-hedge (beli sisi lawan) bila likuid & <= MAX_PRICE.
        opp_ask = best_ask(opp_book)
        if opp_ask is not None and opp_ask <= p.max_price:
            return Hedge(
                token_id=opp_book.token_id,
                outcome=opp.value,
                price=opp_ask,
                hedge_fraction=p.hedge_fraction,
                order_type=ORDER_FAK,
                reason=f"hedge:{reason_suffix}",
            )
        # Prioritas B: exit (jual sisi held) bila ada bid.
        held_bid = best_bid(held_book)
        if held_bid is not None:
            return Exit(
                token_id=held_book.token_id,
                outcome=held.value,
                price=held_bid,
                order_type=ORDER_FAK,
                reason=f"exit:{reason_suffix}",
            )
        # Tak ada likuiditas untuk hedge maupun exit.
        return NoOp(reason=f"trigger_no_liquidity:{reason_suffix}")
