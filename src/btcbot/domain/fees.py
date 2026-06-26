"""domain/fees.py — model biaya taker (pluggable).

Temuan **terverifikasi live** (PROMPT_GUIDE ✅ VERIFIED REALITY #3): market crypto
up/down **BERBIAYA** — ``feesEnabled:true``, ``feeType:"crypto_fees_v2"``,
``rate:0.07``, ``takerOnly:true``. **Asumsi zero-fee SALAH**; semua ``net_edge``/
PnL/backtest/paper/live WAJIB net-of-fee.

Formula presisi ``crypto_fees_v2`` (base notional vs profit) belum di-reverse-
engineer — itu pekerjaan kalibrasi **G1**. Sampai itu, gunakan model konservatif
:class:`ProportionalTakerFee` (fee = ``rate * price`` per share) yang cenderung
**melebih-lebihkan** biaya (aman: tidak mendorong over-trading). Model bersifat
*pluggable* via :class:`FeeModel` agar formula sebenarnya bisa di-drop-in tanpa
mengubah pemanggil. :class:`ZeroFee` disediakan khusus untuk **ablation**
(docs/09 §9.4: PnL dengan vs tanpa fee).

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
    """Model konservatif: ``fee_per_share = rate * price``.

    Args:
        rate: Tarif fee taker (default ``0.07`` = 7%). Wajib di ``[0, 1)``.

    Raises:
        ValueError: bila ``rate`` di luar ``[0, 1)``.

    Note:
        ``# TODO`` reverse-engineer ``crypto_fees_v2`` (base notional vs profit)
        lalu ganti formula ini — kalibrasi di G1.
    """

    rate: Decimal = DEFAULT_FEE_RATE

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.rate < Decimal("1")):
            raise ValueError(f"rate harus di [0, 1), dapat {self.rate}")

    def fee_per_share(self, price: Decimal) -> Decimal:
        """Biaya per share = ``rate * price`` (tidak pernah negatif)."""
        return self.rate * price


@dataclass(frozen=True, slots=True)
class ZeroFee:
    """Model tanpa biaya — HANYA untuk ablation (docs/09). Jangan dipakai live."""

    def fee_per_share(self, price: Decimal) -> Decimal:  # noqa: ARG002 - kontrak
        """Selalu kembalikan ``0`` (mengabaikan ``price``)."""
        return Decimal("0")
