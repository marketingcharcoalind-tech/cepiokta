"""Persistence layer — SQLite/aiosqlite store (docs/07, docs/08 §8.12).

Menyimpan ``rounds``, ``book_snapshots``, ``signals``, ``orders``, ``fills``,
``round_results``, dan ``equity_curve`` sesuai skema docs/07 (diadaptasi untuk
SQLite). Kolom ``mode`` disimpan agar paper vs live dapat dipisah (docs/07 §7.3).

Aturan numerik (docs/03 §3.5): semua harga/uang disimpan sebagai TEXT lalu
diparse kembali menjadi :class:`decimal.Decimal` (presisi terjaga, tanpa float).
Semua waktu disimpan sebagai ISO-8601 UTC dan dikembalikan tz-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import aiosqlite

from btcbot.domain.models import (
    Fill,
    OrderBook,
    Outcome,
    Round,
    RoundResult,
    RoundStatus,
    Signal,
)

if TYPE_CHECKING:
    from types import TracebackType

# --- DDL (idempotent via IF NOT EXISTS) ---

_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS rounds (
        condition_id TEXT,
        round_no INTEGER PRIMARY KEY,
        token_up TEXT,
        token_down TEXT,
        window_start TEXT,
        window_end TEXT,
        start_price TEXT,
        tick_size TEXT,
        min_order_size TEXT,
        status TEXT,
        resolved_outcome TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS book_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round_no INTEGER,
        token_id TEXT,
        ts TEXT,
        best_bid TEXT,
        best_ask TEXT,
        bid_depth TEXT,
        ask_depth TEXT,
        gap INTEGER NOT NULL DEFAULT 0,
        raw TEXT,
        mode TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round_no INTEGER,
        ts TEXT,
        price_now TEXT,
        delta TEXT,
        time_left_sec REAL,
        p_win TEXT,
        leader TEXT,
        ask_win TEXT,
        net_edge TEXT,
        mode TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        client_id TEXT PRIMARY KEY,
        order_id TEXT,
        round_no INTEGER,
        token_id TEXT,
        side TEXT,
        price TEXT,
        size TEXT,
        order_type TEXT,
        status TEXT,
        mode TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT,
        token_id TEXT,
        price TEXT,
        size TEXT,
        ts TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS round_results (
        round_no INTEGER PRIMARY KEY,
        side_taken TEXT,
        entry_price TEXT,
        size TEXT,
        hedge_cost TEXT,
        settled TEXT,
        pnl TEXT,
        balance_after TEXT,
        mode TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS equity_curve (
        ts TEXT PRIMARY KEY,
        balance TEXT,
        mode TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_book_snapshots_round_ts ON book_snapshots(round_no, ts)",
    "CREATE INDEX IF NOT EXISTS idx_signals_round_ts ON signals(round_no, ts)",
)


# --- read DTOs untuk tabel tanpa entitas domain langsung ---


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """Baris ``book_snapshots`` (termasuk penanda gap WSS)."""

    round_no: int
    token_id: str
    ts: datetime
    best_bid: Decimal | None
    best_ask: Decimal | None
    bid_depth: Decimal | None
    ask_depth: Decimal | None
    gap: bool
    raw: str | None
    mode: str


@dataclass(frozen=True, slots=True)
class OrderRow:
    """Baris ``orders`` (gabungan OrderRequest + metadata eksekusi)."""

    client_id: str
    order_id: str
    round_no: int
    token_id: str
    side: str
    price: Decimal
    size: Decimal
    order_type: str
    status: str
    mode: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """Baris ``equity_curve``."""

    ts: datetime
    balance: Decimal
    mode: str


# --- helper serialisasi ---


def db_path_from_url(db_url: str) -> str:
    """Ekstrak path SQLite dari ``DB_URL``.

    Mendukung ``sqlite+aiosqlite:///./file.db``, ``sqlite:///:memory:``, atau
    path langsung (``:memory:`` / ``./file.db``).
    """
    marker = ":///"
    idx = db_url.find(marker)
    if idx == -1:
        return db_url
    return db_url[idx + len(marker) :]


def _dec_to_db(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _dec_from_db(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _req_dec(value: object) -> Decimal:
    """Decimal wajib (kolom NOT NULL secara semantik)."""
    if value is None:
        raise ValueError("nilai Decimal tak boleh None")
    return Decimal(str(value))


def _dt_to_db(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"timestamp harus tz-aware UTC, dapat: {value!r}")
    return value.astimezone(UTC).isoformat()


def _dt_from_db(value: object) -> datetime:
    dt = datetime.fromisoformat(str(value))
    return dt.astimezone(UTC)


def _opt_outcome(value: object) -> Outcome | None:
    return Outcome(str(value)) if value else None


class Store:
    """Wrapper aiosqlite untuk seluruh persistensi btcbot."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def open(cls, db_url: str) -> Store:
        """Buka koneksi DB dari ``DB_URL`` dan buat tabel (idempotent)."""
        conn = await aiosqlite.connect(db_path_from_url(db_url))
        conn.row_factory = aiosqlite.Row
        store = cls(conn)
        await store.create_tables()
        return store

    async def create_tables(self) -> None:
        """Buat semua tabel & index bila belum ada (idempotent)."""
        for ddl in _SCHEMA:
            await self._conn.execute(ddl)
        await self._conn.commit()

    async def close(self) -> None:
        """Tutup koneksi DB."""
        await self._conn.close()

    # ----- rounds -----

    async def upsert_round(self, rnd: Round) -> None:
        """Sisipkan/replace satu ronde (idempotent pada ``round_no``)."""
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO rounds (
                condition_id, round_no, token_up, token_down, window_start,
                window_end, start_price, tick_size, min_order_size, status,
                resolved_outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rnd.condition_id,
                rnd.round_no,
                rnd.token_id_up,
                rnd.token_id_down,
                _dt_to_db(rnd.window_start),
                _dt_to_db(rnd.window_end),
                _dec_to_db(rnd.start_price),
                _dec_to_db(rnd.tick_size),
                _dec_to_db(rnd.min_order_size),
                str(rnd.status),
                None if rnd.resolved_outcome is None else str(rnd.resolved_outcome),
            ),
        )
        await self._conn.commit()

    async def get_round(self, round_no: int) -> Round | None:
        """Ambil ronde berdasarkan ``round_no``."""
        async with self._conn.execute(
            "SELECT * FROM rounds WHERE round_no = ?", (round_no,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Round(
            condition_id=str(row["condition_id"]),
            round_no=int(row["round_no"]),
            token_id_up=str(row["token_up"]),
            token_id_down=str(row["token_down"]),
            window_start=_dt_from_db(row["window_start"]),
            window_end=_dt_from_db(row["window_end"]),
            start_price=_req_dec(row["start_price"]),
            tick_size=_req_dec(row["tick_size"]),
            min_order_size=_req_dec(row["min_order_size"]),
            status=RoundStatus(str(row["status"])),
            resolved_outcome=_opt_outcome(row["resolved_outcome"]),
        )

    async def update_round_status(
        self,
        round_no: int,
        status: RoundStatus,
        resolved_outcome: Outcome | None = None,
    ) -> None:
        """Perbarui status (dan resolusi) sebuah ronde."""
        await self._conn.execute(
            "UPDATE rounds SET status = ?, resolved_outcome = ? WHERE round_no = ?",
            (
                str(status),
                None if resolved_outcome is None else str(resolved_outcome),
                round_no,
            ),
        )
        await self._conn.commit()

    # ----- book_snapshots -----

    async def insert_book_snapshot(
        self,
        round_no: int,
        book: OrderBook,
        *,
        mode: str,
        raw: str | None = None,
    ) -> None:
        """Rekam snapshot orderbook (best bid/ask + depth agregat)."""
        best_bid = book.bids[0].price if book.bids else None
        best_ask = book.asks[0].price if book.asks else None
        bid_depth = sum((lvl.size for lvl in book.bids), Decimal(0)) if book.bids else None
        ask_depth = sum((lvl.size for lvl in book.asks), Decimal(0)) if book.asks else None
        await self._conn.execute(
            """
            INSERT INTO book_snapshots (
                round_no, token_id, ts, best_bid, best_ask, bid_depth,
                ask_depth, gap, raw, mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                round_no,
                book.token_id,
                _dt_to_db(book.ts),
                _dec_to_db(best_bid),
                _dec_to_db(best_ask),
                _dec_to_db(bid_depth),
                _dec_to_db(ask_depth),
                raw,
                mode,
            ),
        )
        await self._conn.commit()

    async def insert_gap(
        self,
        round_no: int,
        ts: datetime,
        *,
        mode: str,
        detail: str = "",
    ) -> None:
        """Tandai gap data (mis. WSS putus) sebagai baris ``gap=1``."""
        await self._conn.execute(
            """
            INSERT INTO book_snapshots (round_no, token_id, ts, gap, raw, mode)
            VALUES (?, '', ?, 1, ?, ?)
            """,
            (round_no, _dt_to_db(ts), detail, mode),
        )
        await self._conn.commit()

    async def get_book_snapshots(self, round_no: int) -> list[BookSnapshot]:
        """Ambil seluruh snapshot (termasuk gap) untuk satu ronde, terurut."""
        async with self._conn.execute(
            "SELECT * FROM book_snapshots WHERE round_no = ? ORDER BY id", (round_no,)
        ) as cur:
            rows = await cur.fetchall()
        return [
            BookSnapshot(
                round_no=int(row["round_no"]),
                token_id=str(row["token_id"]),
                ts=_dt_from_db(row["ts"]),
                best_bid=_dec_from_db(row["best_bid"]),
                best_ask=_dec_from_db(row["best_ask"]),
                bid_depth=_dec_from_db(row["bid_depth"]),
                ask_depth=_dec_from_db(row["ask_depth"]),
                gap=bool(row["gap"]),
                raw=None if row["raw"] is None else str(row["raw"]),
                mode=str(row["mode"]),
            )
            for row in rows
        ]

    # ----- signals -----

    async def insert_signal(self, signal: Signal, *, mode: str) -> None:
        """Rekam satu sinyal/edge."""
        await self._conn.execute(
            """
            INSERT INTO signals (
                round_no, ts, price_now, delta, time_left_sec, p_win, leader,
                ask_win, net_edge, mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.round_no,
                _dt_to_db(signal.ts),
                _dec_to_db(signal.price_now),
                _dec_to_db(signal.delta),
                signal.time_left_sec,
                _dec_to_db(signal.p_win),
                signal.leader,
                _dec_to_db(signal.ask_win),
                _dec_to_db(signal.net_edge),
                mode,
            ),
        )
        await self._conn.commit()

    async def get_signals(self, round_no: int) -> list[Signal]:
        """Ambil seluruh sinyal untuk satu ronde, terurut."""
        async with self._conn.execute(
            "SELECT * FROM signals WHERE round_no = ? ORDER BY id", (round_no,)
        ) as cur:
            rows = await cur.fetchall()
        return [
            Signal(
                round_no=int(row["round_no"]),
                ts=_dt_from_db(row["ts"]),
                price_now=_req_dec(row["price_now"]),
                delta=_req_dec(row["delta"]),
                time_left_sec=float(row["time_left_sec"]),
                p_win=_req_dec(row["p_win"]),
                leader=str(row["leader"]),
                ask_win=_req_dec(row["ask_win"]),
                net_edge=_req_dec(row["net_edge"]),
            )
            for row in rows
        ]

    # ----- orders -----

    async def insert_order(self, order: OrderRow) -> None:
        """Sisipkan/replace satu order (idempotent pada ``client_id``)."""
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO orders (
                client_id, order_id, round_no, token_id, side, price, size,
                order_type, status, mode, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.client_id,
                order.order_id,
                order.round_no,
                order.token_id,
                order.side,
                _dec_to_db(order.price),
                _dec_to_db(order.size),
                order.order_type,
                order.status,
                order.mode,
                _dt_to_db(order.created_at),
            ),
        )
        await self._conn.commit()

    async def get_order(self, client_id: str) -> OrderRow | None:
        """Ambil order berdasarkan ``client_id``."""
        async with self._conn.execute(
            "SELECT * FROM orders WHERE client_id = ?", (client_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return OrderRow(
            client_id=str(row["client_id"]),
            order_id=str(row["order_id"]),
            round_no=int(row["round_no"]),
            token_id=str(row["token_id"]),
            side=str(row["side"]),
            price=_req_dec(row["price"]),
            size=_req_dec(row["size"]),
            order_type=str(row["order_type"]),
            status=str(row["status"]),
            mode=str(row["mode"]),
            created_at=_dt_from_db(row["created_at"]),
        )

    # ----- fills -----

    async def insert_fill(self, fill: Fill) -> None:
        """Rekam satu fill."""
        await self._conn.execute(
            "INSERT INTO fills (order_id, token_id, price, size, ts) VALUES (?, ?, ?, ?, ?)",
            (
                fill.order_id,
                fill.token_id,
                _dec_to_db(fill.price),
                _dec_to_db(fill.size),
                _dt_to_db(fill.ts),
            ),
        )
        await self._conn.commit()

    async def get_fills(self, order_id: str) -> list[Fill]:
        """Ambil seluruh fill untuk satu order, terurut."""
        async with self._conn.execute(
            "SELECT * FROM fills WHERE order_id = ? ORDER BY id", (order_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [
            Fill(
                order_id=str(row["order_id"]),
                token_id=str(row["token_id"]),
                price=_req_dec(row["price"]),
                size=_req_dec(row["size"]),
                ts=_dt_from_db(row["ts"]),
            )
            for row in rows
        ]

    # ----- round_results -----

    async def insert_round_result(self, result: RoundResult, *, mode: str) -> None:
        """Sisipkan/replace hasil PnL satu ronde."""
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO round_results (
                round_no, side_taken, entry_price, size, hedge_cost, settled,
                pnl, balance_after, mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.round_no,
                result.side_taken,
                _dec_to_db(result.entry_price),
                _dec_to_db(result.size),
                _dec_to_db(result.hedge_cost),
                _dec_to_db(result.settled),
                _dec_to_db(result.pnl),
                _dec_to_db(result.balance_after),
                mode,
            ),
        )
        await self._conn.commit()

    async def get_round_result(self, round_no: int) -> RoundResult | None:
        """Ambil hasil PnL satu ronde."""
        async with self._conn.execute(
            "SELECT * FROM round_results WHERE round_no = ?", (round_no,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return RoundResult(
            round_no=int(row["round_no"]),
            side_taken=str(row["side_taken"]),
            entry_price=_req_dec(row["entry_price"]),
            size=_req_dec(row["size"]),
            hedge_cost=_req_dec(row["hedge_cost"]),
            settled=_req_dec(row["settled"]),
            pnl=_req_dec(row["pnl"]),
            balance_after=_req_dec(row["balance_after"]),
        )

    # ----- equity_curve -----

    async def insert_equity_point(self, ts: datetime, balance: Decimal, mode: str) -> None:
        """Sisipkan/replace titik kurva ekuitas (idempotent pada ``ts``)."""
        await self._conn.execute(
            "INSERT OR REPLACE INTO equity_curve (ts, balance, mode) VALUES (?, ?, ?)",
            (_dt_to_db(ts), _dec_to_db(balance), mode),
        )
        await self._conn.commit()

    async def get_equity_curve(self, mode: str | None = None) -> list[EquityPoint]:
        """Ambil kurva ekuitas (opsional difilter per ``mode``), terurut waktu."""
        if mode is None:
            async with self._conn.execute("SELECT * FROM equity_curve ORDER BY ts") as cur:
                rows = await cur.fetchall()
        else:
            async with self._conn.execute(
                "SELECT * FROM equity_curve WHERE mode = ? ORDER BY ts", (mode,)
            ) as cur:
                rows = await cur.fetchall()
        return [
            EquityPoint(
                ts=_dt_from_db(row["ts"]),
                balance=_req_dec(row["balance"]),
                mode=str(row["mode"]),
            )
            for row in rows
        ]

    async def __aenter__(self) -> Store:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
