"""domain/fees.py — model biaya taker crypto_fees_v2 (pluggable).

Temuan **terverifikasi live** (PROMPT_GUIDE ✅ VERIFIED REALITY #3): market crypto
up/down **BERBIAYA** — ``feesEnabled:true``, ``feeType:"crypto_fees_v2"``.
``feeSchedule`` market ``btc-updown-5m`` live = ``{exponent:1, rate:0.07,
takerOnly:true, rebateRate:0.2}`` (lihat ``tests/fixtures/gamma_fee_schedule.json``).
**Asumsi zero-fee SALAH**; semua ``net_edge``/PnL/backtest/paper/live WAJIB net-of-fee.

crypto_fees_v2 VERIFIED: feeSchedule{exponent:1,rate:0.07,takerOnly} →
taker fee = ``rate * min(p, 1-p) ** exponent`` per share. Strategi kita = **taker**;
maker tak bayar (``takerOnly``) → ``rebateRate`` diabaikan di sisi taker.
Simetris: maksimum di ``p=0.5``, menuju 0 di ekstrem; ``exponent>1`` menekan fee
lebih tajam di ekstrem.

Domain murni: tidak mengimpor Settings. Lapisan ``app`` menyuntikkan
``Settings.fee_rate`` / ``Settings.fee_exponent`` saat membangun model.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

# Default crypto_fees_v2 (taker) — terverifikasi dari feeSchedule live.
DEFAULT_FEE_RATE = Decimal("0.07")
DEFAULT_FEE_EXPONENT = 1

_ONE = Decimal("1")
_ZERO = Decimal("0")


def estimate_fee(
    price: Decimal,
    size: Decimal,
    rate: Decimal = DEFAULT_FEE_RATE,
    exponent: int = DEFAULT_FEE_EXPONENT,
) -> Decimal:
    """Biaya taker crypto_fees_v2 untuk ``size`` share pada ``price``.

    ``fee = size * rate * min(price, 1-price) ** exponent``. Linear terhadap
    ``size``; simetris terhadap ``price`` (maks di 0.5). Tidak pernah negatif.

    Args:
        price: Harga eksekusi per share (0..1).
        size: Jumlah share (>= 0).
        rate: Tarif fee taker dasar (default 0.07).
        exponent: Eksponen jarak-ke-ekstrem (default 1).
    """
    edge_dist = min(price, _ONE - price)
    edge_dist = max(edge_dist, _ZERO)  # price di luar [0,1] → clamp jarak ke 0
    return size * rate * (edge_dist**exponent)


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
class CryptoFeesV2:
    """Fee taker crypto_fees_v2: ``fee_per_share = rate * min(p, 1-p) ** exponent``.

    Terverifikasi dari ``feeSchedule`` live ``{exponent:1, rate:0.07, takerOnly}``.
    Untuk entry near-settlement (``price`` 0.80-0.99) fee jadi kecil
    (``rate * (1-price) ** exponent``) tetapi TIDAK nol.

    Args:
        rate: Tarif fee taker dasar (default ``0.07``). Wajib di ``[0, 1)``.
        exponent: Eksponen jarak-ke-ekstrem (default ``1``). Wajib ``>= 1``.

    Raises:
        ValueError: bila ``rate`` di luar ``[0, 1)`` atau ``exponent < 1``.
    """

    rate: Decimal = DEFAULT_FEE_RATE
    exponent: int = DEFAULT_FEE_EXPONENT

    def __post_init__(self) -> None:
        if not (_ZERO <= self.rate < _ONE):
            raise ValueError(f"rate harus di [0, 1), dapat {self.rate}")
        if self.exponent < 1:
            raise ValueError(f"exponent harus >= 1, dapat {self.exponent}")

    def fee_per_share(self, price: Decimal) -> Decimal:
        """Biaya per share = ``rate * min(price, 1-price) ** exponent``."""
        return estimate_fee(price, _ONE, self.rate, self.exponent)


# Alias kompatibilitas: nama lama tetap valid (engine/replay/report).
ProportionalTakerFee = CryptoFeesV2


@dataclass(frozen=True, slots=True)
class ZeroFee:
    """Model tanpa biaya — HANYA untuk ablation (docs/09). Jangan dipakai live."""

    def fee_per_share(self, price: Decimal) -> Decimal:  # noqa: ARG002 - kontrak
        """Selalu kembalikan ``0`` (mengabaikan ``price``)."""
        return _ZERO
