"""Tests for the backtest report CLI (btcbot.backtest.report.main / generate_report)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from btcbot.backtest.replay import ReplayTick
from btcbot.backtest.report import filter_rounds, generate_report, main
from btcbot.config.settings import Settings
from btcbot.data.store import Store
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus, Signal

WS = datetime(2026, 6, 26, 13, 15, tzinfo=UTC)
WE = datetime(2026, 6, 26, 13, 20, tzinfo=UTC)


def _ob(token: str, *, ask: str, bid: str) -> OrderBook:
    return OrderBook(
        token_id=token,
        ts=WE - timedelta(seconds=10),
        bids=[BookLevel(Decimal(bid), Decimal("100"))],
        asks=[BookLevel(Decimal(ask), Decimal("100"))],
    )


async def _seed(store: Store, *, count: int, base_end: datetime = WE) -> None:
    """Seed ``count`` ronde resolved (UP menang) + book + signal."""
    for i in range(count):
        end = base_end - timedelta(minutes=5 * i)
        start = end - timedelta(minutes=5)
        rno = int(end.timestamp())
        up, down = f"up{i}", f"down{i}"
        rnd = Round(
            condition_id=f"0xc{i}",
            round_no=rno,
            token_id_up=up,
            token_id_down=down,
            window_start=start,
            window_end=end,
            start_price=Decimal("65000"),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("1"),
            status=RoundStatus.RESOLVED,
            resolved_outcome=Outcome.UP,
        )
        await store.upsert_round(rnd)
        await store.set_resolution(rno, Outcome.UP)
        ts = end - timedelta(seconds=10)
        await store.insert_book_snapshot(rno, _ob(up, ask="0.90", bid="0.88"), mode="readonly")
        await store.insert_book_snapshot(rno, _ob(down, ask="0.12", bid="0.08"), mode="readonly")
        await store.insert_signal(
            Signal(
                round_no=rno,
                ts=ts,
                price_now=Decimal("65120"),
                delta=Decimal("120"),
                time_left_sec=10.0,
                p_win=Decimal("0"),
                leader="UP",
                ask_win=Decimal("0"),
                net_edge=Decimal("0"),
            ),
            mode="readonly",
        )


@pytest.fixture
async def db_path(tmp_path: Path) -> AsyncIterator[str]:
    path = str(tmp_path / "report_test.db")
    store = await Store.open(path)
    await _seed(store, count=3)
    try:
        yield path
    finally:
        await store.close()


def _settings(db: str) -> Settings:
    return Settings(db_url=db, paper_starting_balance=Decimal("200"), delta_threshold="1")


class TestGenerateReport:
    async def test_core_report_section(self, db_path: str) -> None:
        text = await generate_report(_settings(db_path), db=db_path)
        assert "=== BACKTEST REPORT" in text
        assert "Net PnL (setelah fee)" in text
        assert "reliability curve" in text
        assert "=== G1 PREVIEW ===" in text

    async def test_ablation_section_added(self, db_path: str) -> None:
        text = await generate_report(_settings(db_path), db=db_path, with_ablation=True)
        assert "=== ABLATION" in text
        assert "no_fee" in text

    async def test_grid_section_added(self, db_path: str) -> None:
        text = await generate_report(
            _settings(db_path),
            db=db_path,
            with_grid=True,
            t_entry_values=[20, 10],
            delta_values=[Decimal("1")],
            max_price_values=[Decimal("0.99")],
        )
        assert "=== SENSITIVITY GRID" in text

    async def test_empty_db_friendly_message(self, tmp_path: Path) -> None:
        path = str(tmp_path / "empty.db")
        store = await Store.open(path)
        await store.close()  # tabel dibuat, tanpa ronde
        text = await generate_report(_settings(path), db=path)
        assert "Tidak ada ronde berlabel" in text

    async def test_min_rounds_guard_warns(self, db_path: str) -> None:
        # 3 ronde < min_rounds 300 → peringatan "BELUM CUKUP".
        text = await generate_report(_settings(db_path), db=db_path, min_rounds=300)
        assert "BELUM CUKUP" in text

    async def test_min_rounds_enough(self, db_path: str) -> None:
        text = await generate_report(_settings(db_path), db=db_path, min_rounds=1)
        assert "CUKUP (>= 1)" in text

    async def test_since_filter_excludes_old(self, db_path: str) -> None:
        # since setelah semua window_end → 0 ronde lolos → pesan ramah.
        text = await generate_report(_settings(db_path), db=db_path, since=WE + timedelta(hours=1))
        assert "Tidak ada ronde berlabel" in text

    async def test_max_rounds_limits_loaded(self, db_path: str) -> None:
        # DB punya 3 ronde; limit=1 → hanya 1 ronde di-load & dilaporkan.
        text = await generate_report(_settings(db_path), db=db_path, limit=1, min_rounds=1)
        assert "Rounds total/entered  : 1 / 1" in text

    async def test_grid_without_max_rounds_caps_and_warns(
        self, db_path: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = await generate_report(
            _settings(db_path),
            db=db_path,
            with_grid=True,
            t_entry_values=[20],
            delta_values=[Decimal("1")],
            max_price_values=[Decimal("0.99")],
        )
        assert "=== SENSITIVITY GRID" in text
        assert "tanpa --max-rounds" in capsys.readouterr().err


class TestFilterRounds:
    def _round(self, end: datetime) -> Round:
        return Round(
            condition_id="0xc",
            round_no=int(end.timestamp()),
            token_id_up="u",
            token_id_down="d",
            window_start=end - timedelta(minutes=5),
            window_end=end,
            start_price=Decimal("65000"),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("1"),
            status=RoundStatus.RESOLVED,
            resolved_outcome=Outcome.UP,
        )

    def test_since_until_window(self) -> None:
        rounds: list[tuple[Round, list[ReplayTick]]] = [
            (self._round(WE - timedelta(minutes=5 * i)), []) for i in range(4)
        ]
        out = filter_rounds(
            rounds, since=WE - timedelta(minutes=11), until=WE - timedelta(minutes=4)
        )
        ends = {r.window_end for r, _ in out}
        assert WE - timedelta(minutes=5) in ends
        assert WE - timedelta(minutes=10) in ends
        assert WE not in ends  # di luar until
        assert WE - timedelta(minutes=15) not in ends  # di luar since


class TestMainEntrypoint:
    def test_main_prints_report(self, db_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--db", db_path, "--delta", "1", "--min-rounds", "1"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== BACKTEST REPORT" in out
        assert "=== G1 PREVIEW ===" in out
