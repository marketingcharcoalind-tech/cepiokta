"""Position sizing — fractional Kelly + caps (docs/06 §6.2, docs/08 §8.9).

Fungsi murni (tanpa I/O) untuk menghitung ukuran order dalam satuan *share*.
Ukuran dibatasi oleh empat cap dan sebuah gerbang edge:

1. ``size_kelly``                       — fractional Kelly.
2. ``MAX_NOTIONAL_ROUND / ask``         — cap absolut $ per ronde.
3. ``(bankroll * MAX_BANKROLL_FRACTION) / ask`` — cap % bankroll per ronde.
4. ``depth_available * FILL_SAFETY``    — cap likuiditas.

Gerbang: bila ``edge <= MIN_EDGE`` → ukuran 0 (tidak entry).

Invariant (docs/05 §5.7): never-fade (ukuran hanya untuk sisi leader yang
diberikan pemanggil), tidak beli di atas ``max_price``, dan ``size >= 0``.

Aturan numerik (docs/03 §3.5): semua memakai :class:`decimal.Decimal`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from btcbot.config.settings import Settings

_ZERO = Decimal("0")
_ONE = Decimal("1")


class BindingCap(StrEnum):
    """Cap mana yang menentukan (binding) ukuran akhir."""

    KELLY = "kelly"
    NOTIONAL = "max_notional_round"
    BANKROLL_FRACTION = "max_bankroll_fraction"
    DEPTH = "depth"
    NONE = "none"  # ukuran 0 (tidak entry / input invalid)


@dataclass(frozen=True, slots=True)
class SizingLimits:
    """Parameter & batas sizing (sebagian dari Settings)."""

    kelly_fraction: Decimal
    max_notional_round: Decimal
    max_bankroll_fraction: Decimal
    fill_safety: Decimal
    min_edge: Decimal
    max_price: Decimal = _ONE
    min_order_size: Decimal = _ZERO

    @classmethod
    def from_settings(cls, settings: Settings) -> SizingLimits:
        """Bangun :class:`SizingLimits` dari konfigurasi global."""
        return cls(
            kelly_fraction=settings.kelly_fraction,
            max_notional_round=settings.max_notional_round,
            max_bankroll_fraction=settings.max_bankroll_fraction,
            fill_safety=settings.fill_safety,
            min_edge=settings.min_edge,
            max_price=settings.max_price,
        )


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Hasil perhitungan sizing."""

    size: Decimal  # dalam share (>= 0)
    notional: Decimal  # size * ask ($)
    binding_cap: BindingCap
    edge: Decimal


def _zero_result(edge: Decimal) -> SizingResult:
    return SizingResult(size=_ZERO, notional=_ZERO, binding_cap=BindingCap.NONE, edge=edge)


def compute_size(  # noqa: PLR0913
    *,
    p_win: Decimal,
    ask: Decimal,
    fair_price: Decimal,
    bankroll: Decimal,
    depth_available: Decimal,
    limits: SizingLimits,
    fee: Decimal = _ZERO,
    slippage: Decimal = _ZERO,
) -> SizingResult:
    """Hitung ukuran order (share) untuk sisi leader.

    Args:
        p_win: Probabilitas menang tersestimasi (0..1).
        ask: Harga ask terbaik sisi leader (0..1).
        fair_price: Fair value sisi leader (mis. ``p_win``) untuk hitung edge.
        bankroll: Saldo aktif (paper/live). Lihat :func:`active_bankroll`.
        depth_available: Total size tersedia di book sisi leader (share).
        limits: Batas & parameter sizing.
        fee: Biaya per share.
        slippage: Slippage diharapkan per share.

    Returns:
        :class:`SizingResult` dengan ``size >= 0`` dan cap yang binding.
    """
    edge = fair_price - ask - fee - slippage

    # Input invalid / di luar invariant → tidak entry.
    if ask <= _ZERO or ask > limits.max_price:
        return _zero_result(edge)
    if bankroll <= _ZERO or depth_available < _ZERO:
        return _zero_result(edge)
    if edge <= limits.min_edge:
        return _zero_result(edge)

    # Kelly mentah (never-fade: di-floor ke 0).
    kelly_raw = max(_ZERO, (p_win - (_ONE - p_win) * ask) / ask)
    size_kelly = limits.kelly_fraction * kelly_raw * bankroll / ask

    cap_notional = limits.max_notional_round / ask
    cap_bankroll = (bankroll * limits.max_bankroll_fraction) / ask
    cap_depth = depth_available * limits.fill_safety

    candidates: list[tuple[BindingCap, Decimal]] = [
        (BindingCap.KELLY, size_kelly),
        (BindingCap.NOTIONAL, cap_notional),
        (BindingCap.BANKROLL_FRACTION, cap_bankroll),
        (BindingCap.DEPTH, cap_depth),
    ]
    binding_cap, size = min(candidates, key=lambda kv: kv[1])
    size = max(_ZERO, size)

    # Tidak bisa memenuhi ukuran order minimum → tidak entry.
    if size < limits.min_order_size:
        return _zero_result(edge)

    return SizingResult(size=size, notional=size * ask, binding_cap=binding_cap, edge=edge)


def active_bankroll(settings: Settings, paper_balance: Decimal | None = None) -> Decimal:
    """Tentukan bankroll aktif untuk sizing.

    Saat ``PAPER_TRADING=true``: gunakan ``paper_balance`` (saldo paper
    berjalan) bila diberikan, jika tidak ``PAPER_STARTING_BALANCE``.
    Saldo wallet nyata (live) belum diimplementasikan (Fase 3).

    Raises:
        NotImplementedError: bila dipanggil saat ``PAPER_TRADING=false``
            (jalur live belum tersedia di fase pra-live ini).
    """
    if settings.paper_trading:
        return paper_balance if paper_balance is not None else settings.paper_starting_balance
    raise NotImplementedError(
        "bankroll live belum tersedia (fase pra-live); set PAPER_TRADING=true"
    )
