"""domain/fees.py — model biaya taker (pluggable).

Temuan **terverifikasi live** (PROMPT_GUIDE ✅ VERIFIED REALITY #3): market crypto
up/down **BERBIAYA** — ``feesEnabled:true``, ``feeType:"crypto_fees_v2"``,
``rate:0.07``, ``takerOnly:true``. **Asumsi zero-fee SALAH**; semua ``net_edge``/
PnL/backtest/paper/live WAJIB net-of-fee.

Formula ``crypto_fees_v2`` (terverifikasi): ``fee_per_share = rate * min(p, 1-p)``
dengan ``rate`` default ``0.07`` (7%). Simetris: maksimum di ``p=0.5``, menuju 0
di ekstrem. Model konservatif & *pluggable* via :class:`FeeModel`. :class:`ZeroFee`
disediakan khusus untuk **ablation** (docs/09 §9.4: PnL dengan vs tanpa fee).

Domain murni: tidak mengimpor Settings. Lapisan ``app`` menyuntikkan
``Settings.fee_rate`` saat membangun model.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

# Default konservatif (taker) — selaras data live crypto_fees_v2.
DEFAULT_FEE_RATE = Decimal("0.07")


@runtime_checkable
class FeeModel(Protocol):
    """Kontrak biaya per share untuk order taker.

    ``price`` = harga eksekusi per share (mis. ``ask_win`` sisi yang dibeli),
    dalam unit dolar [0, 1] (payout settle ke $1/$0).
    """

    def fee_per_share(self, price: Decimal) -> Decimal:
        """Kembalikan biaya (dolar) per share pada harga eksekusi ``price``."""
        ...


@dataclass(frozen=True, slots=True)
class ProportionalTakerFee:
    """Model fee taker ``crypto_fees_v2``: ``fee_per_share = rate * min(price, 1-price)``.

    Formula terverifikasi Polymarket (crypto up/down): fee per share simetris —
    maksimum di ``price=0.5`` (``rate/2``) dan mendekati 0 di ekstrem (0/1).
    Untuk entry near-settlement (``price`` 0.80-0.99) fee jadi kecil
    (``rate * (1-price)``), tetapi TIDAK nol (asumsi zero-fee SALAH).

    Args:
        rate: Tarif fee taker dasar (default ``0.07`` = 7%). Wajib di ``[0, 1)``.

    Raises:
        ValueError: bila ``rate`` di luar ``[0, 1)``.
    """

    rate: Decimal = DEFAULT_FEE_RATE

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.rate < Decimal("1")):
            raise ValueError(f"rate harus di [0, 1), dapat {self.rate}")

    def fee_per_share(self, price: Decimal) -> Decimal:
        """Biaya per share = ``rate * min(price, 1 - price)`` (tidak pernah negatif)."""
        return self.rate * min(price, Decimal("1") - price)


@dataclass(frozen=True, slots=True)
class ZeroFee:
    """Model tanpa biaya — HANYA untuk ablation (docs/09). Jangan dipakai live."""

    def fee_per_share(self, price: Decimal) -> Decimal:  # noqa: ARG002 - kontrak
        """Selalu kembalikan ``0`` (mengabaikan ``price``)."""
        return Decimal("0")
