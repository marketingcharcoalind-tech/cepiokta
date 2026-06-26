"""Resolution recorder — labeli outcome ronde (Fase 1).

Mengisi ``rounds.resolved_outcome`` (+ ``status='resolved'``) untuk ronde yang
window-nya sudah lewat. **Read-only** (tanpa order/private key).

Sumber outcome:
1. **PRIMER — Gamma** (ground truth yang dibayar): ``ResolutionLookup.get_resolution``
   membaca ``outcomePrices``/``closed``/``umaResolutionStatus`` (lihat
   :func:`~btcbot.adapters.gamma.parse_resolution`).
2. **CROSS-CHECK — Chainlink** (best-effort, hanya untuk ronde yang baru saja
   berakhir): bandingkan harga settlement vs ``start_price``. Bila berbeda dari
   Gamma → log ``resolution_mismatch`` (menyingkap selisih Data Feeds vs Data
   Streams → masukan B2b). ``settlement_price`` disimpan bila tersedia.

Idempoten: ronde yang sudah ``resolved`` tidak ditimpa.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import structlog

from btcbot.adapters.chainlink import PriceUnavailableError
from btcbot.domain.models import Outcome

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from btcbot.adapters.clock import Clock
    from btcbot.data.store import Store
    from btcbot.domain.models import PriceSource, Round

_log = structlog.get_logger("btcbot.resolver")

# Cross-check Chainlink hanya bermakna bila window baru saja berakhir
# (harga "sekarang" ≈ settlement). Untuk backfill ronde lama → dilewati.
_DEFAULT_CROSS_CHECK_MAX_SEC = 120


class ResolutionLookup(Protocol):
    """Kontrak sumber outcome ground-truth (diimplementasikan Gamma client)."""

    async def get_resolution(self, condition_id: str) -> Outcome | None:
        """Outcome RESOLVED, atau ``None`` bila belum resolved/tak ditemukan."""
        ...


class Resolver:
    """Perekam resolusi ronde (Gamma primer + cross-check Chainlink).

    Args:
        store: Persistensi (rounds).
        lookup: Sumber outcome ground-truth (Gamma).
        clock: Sumber waktu (untuk memilih ronde yang due & cross-check window).
        price_source: Chainlink (opsional) untuk cross-check & settlement_price.
        cross_check_max_sec: Umur maksimum sejak ``window_end`` agar cross-check
            Chainlink dilakukan (default 120s). Lebih tua → skip (backfill).
    """

    def __init__(
        self,
        store: Store,
        lookup: ResolutionLookup,
        clock: Clock,
        *,
        price_source: PriceSource | None = None,
        cross_check_max_sec: int = _DEFAULT_CROSS_CHECK_MAX_SEC,
    ) -> None:
        self._store = store
        self._lookup = lookup
        self._clock = clock
        self._price_source = price_source
        self._cross_check_max_sec = cross_check_max_sec

    async def resolve_round(self, rnd: Round) -> bool:
        """Resolusikan satu ronde. Kembalikan True bila berhasil diberi label.

        Idempoten: bila sudah resolved atau Gamma belum resolve → tidak menulis.
        """
        if str(rnd.status) == "resolved":
            return False
        outcome = await self._lookup.get_resolution(rnd.condition_id)
        if outcome is None:
            return False  # belum resolved → coba lagi nanti

        settlement_price = await self._cross_check(rnd, outcome)
        await self._store.set_resolution(
            rnd.round_no,
            outcome,
            settlement_price=settlement_price,
            resolution_source="gamma",
        )
        _log.info(
            "round_resolved",
            round_no=rnd.round_no,
            outcome=str(outcome),
            settlement_price=None if settlement_price is None else str(settlement_price),
        )
        return True

    async def _cross_check(self, rnd: Round, gamma_outcome: Outcome) -> Decimal | None:
        """Cross-check Chainlink (best-effort); kembalikan settlement_price|None."""
        if self._price_source is None:
            return None
        age_sec = (self._clock.now() - rnd.window_end).total_seconds()
        if age_sec > self._cross_check_max_sec:
            return None  # ronde lama (backfill) → cross-check tak bermakna
        try:
            tick = await self._price_source.price_now()
        except PriceUnavailableError:
            return None
        chainlink_outcome = Outcome.UP if tick.price >= rnd.start_price else Outcome.DOWN
        if chainlink_outcome != gamma_outcome:
            _log.warning(
                "resolution_mismatch",
                round_no=rnd.round_no,
                gamma=str(gamma_outcome),
                chainlink=str(chainlink_outcome),
                settlement_price=str(tick.price),
                start_price=str(rnd.start_price),
            )
        return tick.price

    async def resolve_due(self, now: datetime | None = None, *, limit: int | None = None) -> int:
        """Resolusikan semua ronde yang due (``window_end < now`` & belum resolved)."""
        moment = now or self._clock.now()
        rounds = await self._store.get_unresolved_rounds(moment, limit=limit)
        resolved = 0
        for rnd in rounds:
            if await self.resolve_round(rnd):
                resolved += 1
        return resolved

    async def backfill(self) -> int:
        """Sapu SEMUA ronde belum-resolve yang window-nya sudah lewat."""
        return await self.resolve_due(self._clock.now())
