"""Demo/fixture runtime untuk readonly (docs/08 §8.14, DoD ``make run-readonly``).

Menyediakan adapter sintetis deterministik agar :mod:`btcbot.app.cli` dapat
dijalankan end-to-end **tanpa jaringan** dan **tanpa mengirim order**. Dipakai
oleh flag ``--demo``.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from btcbot.adapters.chainlink import FakePriceSource
from btcbot.adapters.clob_ws import HttpClobWS, WSConnection, WSConnectionClosedError
from btcbot.adapters.clock import SimClock
from btcbot.data.recorder import Recorder
from btcbot.data.store import Store
from btcbot.domain.models import MarketStatus, Outcome, RoundMeta

if TYPE_CHECKING:
    from btcbot.config.settings import Settings

_BASE_TS = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


class DemoGamma:
    """GammaClient sintetis: ronde berurutan (durasi 5m) & resolusi UP."""

    def __init__(self, n_rounds: int = 100) -> None:
        self._n = n_rounds
        self._idx = 0
        self._by_condition: dict[str, RoundMeta] = {}

    def _make(self, i: int) -> RoundMeta:
        round_no = 48000 + i
        condition_id = f"0xdemo{round_no}"
        start = _BASE_TS + timedelta(minutes=5 * i)
        meta = RoundMeta(
            market_id=str(round_no),
            condition_id=condition_id,
            slug=f"bitcoin-up-or-down-demo-{round_no}",
            token_id_up=f"up-{round_no}",
            token_id_down=f"down-{round_no}",
            start_time=start,
            end_time=start + timedelta(minutes=5),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            status=MarketStatus.OPEN,
        )
        self._by_condition[condition_id] = meta
        return meta

    async def discover_active_round(self) -> RoundMeta:
        meta = self._make(self._idx)
        self._idx += 1
        return meta

    async def discover_rounds(self) -> list[RoundMeta]:
        return [self._make(self._idx)]

    async def get_market(self, condition_id: str) -> RoundMeta:
        base = self._by_condition.get(condition_id) or self._make(0)
        # Resolusi deterministik: UP.
        return dataclasses.replace(base, status=MarketStatus.RESOLVED, outcome=Outcome.UP)


class _DemoConnection:
    """Koneksi WS sintetis: emit beberapa snapshot lalu 'putus'."""

    def __init__(self, token_ids: list[str], updates: int = 3) -> None:
        self._script: list[str] = []
        for n in range(updates):
            token = token_ids[n % len(token_ids)] if token_ids else "demo"
            self._script.append(
                json.dumps(
                    {
                        "event_type": "book",
                        "asset_id": token,
                        "timestamp": "2026-01-01T00:00:00Z",
                        "bids": [{"price": "0.52", "size": "100"}],
                        "asks": [{"price": "0.55", "size": "80"}],
                    }
                )
            )
        self._idx = 0

    async def send(self, _message: str) -> None:
        return None

    async def recv(self) -> str:
        if self._idx >= len(self._script):
            raise WSConnectionClosedError("demo stream selesai")
        msg = self._script[self._idx]
        self._idx += 1
        return msg

    async def close(self) -> None:
        return None


def _demo_ws(token_updates: int = 3) -> HttpClobWS:
    """WS demo: tiap koneksi baru memutar ulang beberapa snapshot lalu putus."""

    async def factory(_url: str) -> WSConnection:
        return _DemoConnection(["up", "down"], updates=token_updates)

    async def _no_sleep(_seconds: float) -> None:
        return None

    return HttpClobWS(
        "wss://demo",
        connect=factory,
        clock=SimClock(_BASE_TS),
        sleep=_no_sleep,
        max_reconnects=0,
    )


async def build_demo_runtime(settings: Settings) -> tuple[Store, DemoGamma, Recorder]:
    """Bangun store + adapter fixture + recorder (readonly, tanpa order)."""
    store = await Store.open(settings.db_url)
    clock = SimClock(_BASE_TS)
    gamma = DemoGamma()
    ws = _demo_ws()
    # Δ vs start 64000 = +91 (tick price truth sintetis).
    price_source = FakePriceSource(Decimal("64091"), ts=_BASE_TS, source="demo", round_id=1)
    recorder = Recorder(store, ws, price_source, clock, mode=str(settings.mode))
    ws.set_event_sink(recorder.on_circuit_event)
    return store, gamma, recorder
