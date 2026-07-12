"""Pure intra-market UP+DOWN lock-pair arbitrage detection.

This module is domain-only: no database, network, OMS, signing, or secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcbot.domain.fees import FeeModel
from btcbot.domain.models import OrderBook

_ONE = Decimal("1")
_ZERO = Decimal("0")

REJECT_EMPTY_BOOK = "empty_book"
REJECT_SUM_ASKS_HIGH = "sum_asks_too_high"
REJECT_EDGE_LOW = "net_lock_edge_too_low"
REJECT_DEPTH_LOW = "depth_insufficient"
REJECT_REASONS: tuple[str, ...] = (
    REJECT_EMPTY_BOOK,
    REJECT_SUM_ASKS_HIGH,
    REJECT_EDGE_LOW,
    REJECT_DEPTH_LOW,
)


@dataclass(frozen=True, slots=True)
class ArbOpportunity:
    """Evaluation of one synchronized UP/DOWN book pair."""

    round_no: int
    ts: object
    token_up: str
    token_down: str
    ask_up: Decimal | None
    ask_down: Decimal | None
    depth_up: Decimal
    depth_down: Decimal
    sum_asks: Decimal | None
    fee_total: Decimal
    slippage_buffer: Decimal
    net_lock_edge: Decimal | None
    max_lock_size: Decimal
    valid: bool
    reject_reason: str | None


def _best_ask(book: OrderBook) -> tuple[Decimal, Decimal] | None:
    if not book.asks:
        return None
    price = min(level.price for level in book.asks)
    depth = sum((level.size for level in book.asks if level.price == price), _ZERO)
    return price, depth


def detect_lock_pair(  # noqa: PLR0913
    *,
    round_no: int,
    book_up: OrderBook,
    book_down: OrderBook,
    fee_model: FeeModel,
    slippage_buffer: Decimal,
    min_lock_edge: Decimal,
    min_depth: Decimal,
    max_sum_asks: Decimal = _ONE,
) -> ArbOpportunity:
    """Evaluate a book pair and return a valid opportunity or explicit rejection.

    ``net_lock_edge = 1 - ask_up - ask_down - fee_up - fee_down - slippage``.
    The function is deterministic and has no side effects.
    """
    up = _best_ask(book_up)
    down = _best_ask(book_down)
    ts = max(book_up.ts, book_down.ts)
    if up is None or down is None:
        return ArbOpportunity(
            round_no,
            ts,
            book_up.token_id,
            book_down.token_id,
            None if up is None else up[0],
            None if down is None else down[0],
            _ZERO if up is None else up[1],
            _ZERO if down is None else down[1],
            None,
            _ZERO,
            slippage_buffer,
            None,
            _ZERO,
            False,
            REJECT_EMPTY_BOOK,
        )

    ask_up, depth_up = up
    ask_down, depth_down = down
    sum_asks = ask_up + ask_down
    fee_total = fee_model.fee_per_share(ask_up) + fee_model.fee_per_share(ask_down)
    net_edge = _ONE - sum_asks - fee_total - slippage_buffer
    max_size = min(depth_up, depth_down)

    reason: str | None = None
    if sum_asks >= max_sum_asks:
        reason = REJECT_SUM_ASKS_HIGH
    elif net_edge < min_lock_edge:
        reason = REJECT_EDGE_LOW
    elif max_size < min_depth:
        reason = REJECT_DEPTH_LOW

    return ArbOpportunity(
        round_no=round_no,
        ts=ts,
        token_up=book_up.token_id,
        token_down=book_down.token_id,
        ask_up=ask_up,
        ask_down=ask_down,
        depth_up=depth_up,
        depth_down=depth_down,
        sum_asks=sum_asks,
        fee_total=fee_total,
        slippage_buffer=slippage_buffer,
        net_lock_edge=net_edge,
        max_lock_size=max_size,
        valid=reason is None,
        reject_reason=reason,
    )
