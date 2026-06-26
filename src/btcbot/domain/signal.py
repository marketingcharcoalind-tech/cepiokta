"""domain/signal.py — SignalEngine ("trend"/edge math) (docs/05 §5.1-5.3, docs/08 §8.7).

Hitung sinyal/edge satu tick sebagai domain **murni & deterministik** (tanpa I/O):

- ``Δ = price_now - start_price``
- ``time_left`` = ``window_end - now`` (detik, di-clamp ``>= 0``)
- ``leader`` = ``UP`` bila ``Δ > 0`` else ``DOWN`` (docs/05 §5.4)
- ``sigma_left = vol * sqrt(time_left)`` (``vol`` = est. volatilitas per √detik)
- ``z = Δ / max(sigma_left, eps)``
- ``p_win`` = CDF normal standar dari ``|z|`` (probabilitas sisi pemimpin menang)
- ``ask_win`` = best ask token sisi pemimpin (dari order book)
- ``net_edge = p_win - ask_win - fee_per_share - expected_slippage``

Fee **tidak nol** (PROMPT_GUIDE ✅ VERIFIED REALITY #3): ``fee_per_share`` berasal
dari :class:`~btcbot.domain.fees.FeeModel` yang di-inject (default
``crypto_fees_v2`` ~7% taker). ``sigma`` (lewat ``vol``) dikalibrasi di **G1** dari
realized vol. **JANGAN** mengasumsikan zero-fee.

Catatan kontrak: tanda tangan docs/08 §8.7 (``compute(round, price_now, now, vol)``)
diperluas dengan order book (sumber ``ask_win``) + ``expected_slippage``, karena
``net_edge`` mustahil dihitung tanpa harga ask pasar. FeeModel di-inject di
konstruktor agar tetap *pluggable* (lihat docs/08 §8.7 yang sudah disinkronkan).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import TYPE_CHECKING

from btcbot.domain.fees import FeeModel, ProportionalTakerFee
from btcbot.domain.models import Outcome, Signal

if TYPE_CHECKING:
    from datetime import datetime

    from btcbot.domain.models import OrderBook, Round

_SQRT2 = math.sqrt(2.0)
# eps kecil agar pembagian z aman saat sigma_left -> 0 (mis. di ujung window).
_DEFAULT_EPS = 1e-12


def normal_cdf(x: float | Decimal) -> Decimal:
    """CDF normal standar Φ(x) memakai ``math.erf`` (murni, deterministik).

    ``Φ(x) = 0.5 * (1 + erf(x / sqrt(2)))``. Hasil dikembalikan sebagai
    :class:`Decimal` (probabilitas [0, 1]).
    """
    return Decimal(str(0.5 * (1.0 + math.erf(float(x) / _SQRT2))))


def best_ask(book: OrderBook | None) -> Decimal | None:
    """Kembalikan harga ask terbaik (terendah) dari ``book``; None bila kosong."""
    if book is None or not book.asks:
        return None
    return min(level.price for level in book.asks)


class SignalEngine:
    """Mesin perhitungan sinyal/edge (docs/05). FeeModel di-inject (pluggable).

    Args:
        fee_model: Model biaya taker per share. Default
            :class:`~btcbot.domain.fees.ProportionalTakerFee` (~7% konservatif).
        eps: Lantai pembagi ``z`` saat ``sigma_left`` mendekati 0 (default 1e-12).
    """

    def __init__(self, *, fee_model: FeeModel | None = None, eps: float = _DEFAULT_EPS) -> None:
        self._fee: FeeModel = fee_model or ProportionalTakerFee()
        self._eps = eps

    def compute(  # noqa: PLR0913 - parameter eksplisit demi fungsi murni
        self,
        rnd: Round,
        price_now: Decimal,
        now: datetime,
        vol: Decimal,
        *,
        book_up: OrderBook | None = None,
        book_down: OrderBook | None = None,
        expected_slippage: Decimal = Decimal("0"),
    ) -> Signal:
        """Hitung :class:`Signal` untuk satu tick (murni & deterministik).

        Args:
            rnd: Ronde aktif (sumber ``start_price`` & ``window_end``).
            price_now: Harga BTC/USD terkini (Chainlink), Decimal.
            now: Waktu tick (tz-aware UTC).
            vol: Estimasi volatilitas per √detik (Decimal, ``>= 0``).
            book_up: Order book token UP (untuk ``ask_win`` bila UP memimpin).
            book_down: Order book token DOWN (untuk ``ask_win`` bila DOWN memimpin).
            expected_slippage: Perkiraan slippage per share (Decimal, ``>= 0``).

        Returns:
            :class:`Signal` berisi Δ, ``time_left_sec``, ``p_win``, ``leader``,
            ``ask_win``, dan ``net_edge`` (net-of-fee).

        Note:
            Bila order book sisi pemimpin kosong/absen, ``ask_win`` di-set ke
            ``1`` (tidak ada likuiditas → ``net_edge <= 0`` → tidak entry).
        """
        delta = price_now - rnd.start_price
        time_left_sec = max(0.0, (rnd.window_end - now).total_seconds())

        leader = Outcome.UP if delta > 0 else Outcome.DOWN

        # sigma_left = vol * sqrt(time_left); z = delta / max(sigma_left, eps).
        sigma_left = float(vol) * math.sqrt(time_left_sec)
        z = float(delta) / max(sigma_left, self._eps)
        p_win = normal_cdf(abs(z))

        leader_book = book_up if leader is Outcome.UP else book_down
        ask = best_ask(leader_book)
        # Tanpa likuiditas sisi pemimpin → harga tak terjangkau (edge <= 0).
        ask_win = ask if ask is not None else Decimal("1")

        fee_per_share = self._fee.fee_per_share(ask_win)
        net_edge = p_win - ask_win - fee_per_share - expected_slippage

        return Signal(
            round_no=rnd.round_no,
            ts=now,
            price_now=price_now,
            delta=delta,
            time_left_sec=time_left_sec,
            p_win=p_win,
            leader=leader.value,
            ask_win=ask_win,
            net_edge=net_edge,
        )
