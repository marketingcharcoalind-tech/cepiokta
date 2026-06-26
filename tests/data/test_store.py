"""Unit tests for btcbot.data.store (SQLite in-memory CRUD)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from btcbot.data.store import OrderRow, Store, db_path_from_url
from btcbot.domain.models import (
    BookLevel,
    Fill,
    OrderBook,
    Outcome,
    Round,
    RoundResult,
    RoundStatus,
    Signal,
)

WS = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
WE = datetime(2026, 6, 25, 10, 5, tzinfo=UTC)


@pytest.fixture
async def store() -> AsyncIterator[Store]:
    s = await Store.open("sqlite+aiosqlite:///:memory:")
    try:
        yield s
    finally:
        await s.close()


def _round(round_no: int = 48247, status: RoundStatus = RoundStatus.ACTIVE) -> Round:
    return Round(
        condition_id="0xabc",
        round_no=round_no,
        token_id_up="111",
        token_id_down="222",
        window_start=WS,
        window_end=WE,
        start_price=Decimal("64250.50"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        status=status,
    )


class TestDbPathFromUrl:
    def test_aiosqlite_file(self) -> None:
        assert db_path_from_url("sqlite+aiosqlite:///./btcbot.db") == "./btcbot.db"

    def test_memory(self) -> None:
        assert db_path_from_url("sqlite+aiosqlite:///:memory:") == ":memory:"

    def test_plain_path(self) -> None:
        assert db_path_from_url(":memory:") == ":memory:"


class TestSchema:
    async def test_create_tables_idempotent(self, store: Store) -> None:
        # open() sudah membuat tabel; panggil lagi tidak boleh error.
        await store.create_tables()
        await store.create_tables()
        # masih bisa menulis & membaca
        await store.upsert_round(_round())
        assert await store.get_round(48247) is not None

    async def test_migration_idempotent_resolution_columns(self, store: Store) -> None:
        # Jalankan migrasi berkali-kali aman; kolom resolusi tersedia.
        await store.create_tables()
        await store.create_tables()
        await store.upsert_round(_round())
        res = await store.get_resolution(48247)
        assert res is not None
        assert res.settlement_price is None
        assert res.resolution_source is None


class TestResolutionColumns:
    async def test_set_and_get_resolution(self, store: Store) -> None:
        await store.upsert_round(_round())
        await store.set_resolution(
            48247, Outcome.UP, settlement_price=Decimal("64321.5"), resolution_source="gamma"
        )
        res = await store.get_resolution(48247)
        assert res is not None
        assert res.status == "resolved"
        assert res.resolved_outcome is Outcome.UP
        assert res.settlement_price == Decimal("64321.5")
        assert res.resolution_source == "gamma"

    async def test_get_unresolved_rounds(self, store: Store) -> None:
        past = datetime(2026, 6, 25, 9, 0, tzinfo=UTC)
        # ronde lampau belum resolved
        await store.upsert_round(_round(round_no=1, status=RoundStatus.ACTIVE))
        # ronde lampau sudah resolved
        await store.upsert_round(_round(round_no=2, status=RoundStatus.ACTIVE))
        await store.set_resolution(2, Outcome.UP)
        before = datetime(2026, 6, 25, 11, 0, tzinfo=UTC)  # > window_end (10:05)
        unresolved = await store.get_unresolved_rounds(before)
        nos = {r.round_no for r in unresolved}
        assert 1 in nos
        assert 2 not in nos  # sudah resolved → tidak termasuk
        assert past < before  # sanity


class TestRounds:
    async def test_upsert_and_get(self, store: Store) -> None:
        await store.upsert_round(_round())
        got = await store.get_round(48247)
        assert got is not None
        assert got.start_price == Decimal("64250.50")
        assert isinstance(got.start_price, Decimal)
        assert got.window_start == WS
        assert got.window_start.tzinfo is not None
        assert got.status is RoundStatus.ACTIVE
        assert got.resolved_outcome is None

    async def test_get_missing_returns_none(self, store: Store) -> None:
        assert await store.get_round(999) is None

    async def test_upsert_is_idempotent(self, store: Store) -> None:
        await store.upsert_round(_round())
        await store.upsert_round(_round(status=RoundStatus.CLOSED))
        got = await store.get_round(48247)
        assert got is not None
        assert got.status is RoundStatus.CLOSED

    async def test_update_status_and_resolution(self, store: Store) -> None:
        await store.upsert_round(_round())
        await store.update_round_status(48247, RoundStatus.RESOLVED, Outcome.UP)
        got = await store.get_round(48247)
        assert got is not None
        assert got.status is RoundStatus.RESOLVED
        assert got.resolved_outcome is Outcome.UP


class TestBookSnapshots:
    async def test_insert_and_get(self, store: Store) -> None:
        book = OrderBook(
            token_id="111",
            ts=WS,
            bids=[
                BookLevel(Decimal("0.52"), Decimal("100")),
                BookLevel(Decimal("0.51"), Decimal("50")),
            ],
            asks=[BookLevel(Decimal("0.55"), Decimal("80"))],
        )
        await store.insert_book_snapshot(48247, book, mode="readonly")
        snaps = await store.get_book_snapshots(48247)
        assert len(snaps) == 1
        snap = snaps[0]
        assert snap.best_bid == Decimal("0.52")
        assert snap.best_ask == Decimal("0.55")
        assert snap.bid_depth == Decimal("150")
        assert snap.ask_depth == Decimal("80")
        assert snap.gap is False
        assert snap.mode == "readonly"

    async def test_empty_book(self, store: Store) -> None:
        book = OrderBook(token_id="111", ts=WS, bids=[], asks=[])
        await store.insert_book_snapshot(48247, book, mode="readonly")
        snap = (await store.get_book_snapshots(48247))[0]
        assert snap.best_bid is None
        assert snap.bid_depth is None

    async def test_gap_marker(self, store: Store) -> None:
        await store.insert_gap(48247, WS, mode="readonly", detail="disconnected:WSS down")
        snaps = await store.get_book_snapshots(48247)
        assert len(snaps) == 1
        assert snaps[0].gap is True
        assert snaps[0].raw == "disconnected:WSS down"


class TestSignals:
    async def test_insert_and_get(self, store: Store) -> None:
        sig = Signal(
            round_no=48247,
            ts=WS,
            price_now=Decimal("64200"),
            delta=Decimal("-50.5"),
            time_left_sec=18.0,
            p_win=Decimal("0.91"),
            leader="DOWN",
            ask_win=Decimal("0.88"),
            net_edge=Decimal("0.02"),
        )
        await store.insert_signal(sig, mode="readonly")
        got = await store.get_signals(48247)
        assert len(got) == 1
        assert got[0].delta == Decimal("-50.5")
        assert got[0].p_win == Decimal("0.91")
        assert got[0].leader == "DOWN"
        assert got[0].time_left_sec == 18.0


class TestOrders:
    async def test_insert_and_get(self, store: Store) -> None:
        order = OrderRow(
            client_id="cid-1",
            order_id="oid-1",
            round_no=48247,
            token_id="111",
            side="BUY",
            price=Decimal("0.96"),
            size=Decimal("10"),
            order_type="FOK",
            status="filled",
            mode="paper",
            created_at=WS,
        )
        await store.insert_order(order)
        got = await store.get_order("cid-1")
        assert got is not None
        assert got.price == Decimal("0.96")
        assert got.mode == "paper"
        assert got.created_at == WS

    async def test_idempotent_on_client_id(self, store: Store) -> None:
        base = OrderRow(
            client_id="cid-1",
            order_id="oid-1",
            round_no=48247,
            token_id="111",
            side="BUY",
            price=Decimal("0.96"),
            size=Decimal("10"),
            order_type="FOK",
            status="open",
            mode="paper",
            created_at=WS,
        )
        await store.insert_order(base)
        await store.insert_order(
            OrderRow(
                client_id="cid-1",
                order_id="oid-1",
                round_no=48247,
                token_id="111",
                side="BUY",
                price=Decimal("0.96"),
                size=Decimal("10"),
                order_type="FOK",
                status="filled",
                mode="paper",
                created_at=WS,
            )
        )
        got = await store.get_order("cid-1")
        assert got is not None
        assert got.status == "filled"


class TestFills:
    async def test_insert_and_get(self, store: Store) -> None:
        await store.insert_fill(
            Fill(order_id="oid-1", token_id="111", price=Decimal("0.96"), size=Decimal("4"), ts=WS)
        )
        await store.insert_fill(
            Fill(order_id="oid-1", token_id="111", price=Decimal("0.97"), size=Decimal("6"), ts=WE)
        )
        fills = await store.get_fills("oid-1")
        assert len(fills) == 2
        assert fills[0].price == Decimal("0.96")
        assert fills[1].size == Decimal("6")


class TestRoundResults:
    async def test_insert_and_get(self, store: Store) -> None:
        result = RoundResult(
            round_no=48247,
            side_taken="DOWN",
            entry_price=Decimal("0.96"),
            size=Decimal("10"),
            hedge_cost=Decimal("0.5"),
            settled=Decimal("10"),
            pnl=Decimal("-0.1"),
            balance_after=Decimal("99.9"),
        )
        await store.insert_round_result(result, mode="paper")
        got = await store.get_round_result(48247)
        assert got is not None
        assert got.pnl == Decimal("-0.1")
        assert got.balance_after == Decimal("99.9")


class TestEquityCurve:
    async def test_insert_and_get_filtered(self, store: Store) -> None:
        t1 = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
        t2 = datetime(2026, 6, 25, 10, 5, tzinfo=UTC)
        t3 = datetime(2026, 6, 25, 10, 10, tzinfo=UTC)
        await store.insert_equity_point(t1, Decimal("100"), "paper")
        await store.insert_equity_point(t2, Decimal("101.5"), "paper")
        await store.insert_equity_point(t3, Decimal("50"), "live")
        paper = await store.get_equity_curve("paper")
        assert [p.balance for p in paper] == [Decimal("100"), Decimal("101.5")]
        all_points = await store.get_equity_curve()
        assert len(all_points) == 3

    async def test_idempotent_on_ts(self, store: Store) -> None:
        t1 = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
        await store.insert_equity_point(t1, Decimal("100"), "paper")
        await store.insert_equity_point(t1, Decimal("200"), "paper")
        points = await store.get_equity_curve()
        assert len(points) == 1
        assert points[0].balance == Decimal("200")
