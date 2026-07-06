"""Regression test: book_snapshots PRIMARY KEY must work correctly.

ROOT CAUSE (discovered Jul 2026): De-dup historis pakai `CREATE TABLE AS SELECT`
merusak kolom PRIMARY KEY pada tabel `book_snapshots`. Akibatnya `id` berhenti
jadi INTEGER PRIMARY KEY AUTOINCREMENT, dan baris baru yang ditulis bot bisa
dapat `id=NULL`. Ini pernah bikin de-dup berbasis `id` menghapus ~256 ronde.

This test verifies:
1. INSERT baris baru selalu dapat id non-NULL & auto-increment
2. Prosedur de-dup (manual cleanup) tidak menghapus ronde & tidak merusak PK
3. Schema book_snapshots memiliki PRIMARY KEY AUTOINCREMENT yang benar

Test ini RUNTIME (bukan mock) - membuat DB SQLite sementara yang sebenarnya,
insert data, lakukan de-dup, lalu assert.
"""

import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import aiosqlite
import pytest

from btcbot.data.store import Store
from btcbot.domain.models import BookLevel, OrderBook


@pytest.fixture
async def temp_db() -> str:
    """Create temporary SQLite database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
async def temp_store(temp_db: str) -> Store:
    """Create Store with temporary database."""
    store = await Store.open(f"sqlite+aiosqlite:///{temp_db}")
    try:
        yield store
    finally:
        await store.close()


class TestBookSnapshotsPrimaryKey:
    """Test book_snapshots table PRIMARY KEY correctness (runtime, not mock)."""

    async def test_schema_has_correct_primary_key(self, temp_db: str) -> None:
        """Verify book_snapshots schema has INTEGER PRIMARY KEY AUTOINCREMENT."""
        store = await Store.open(f"sqlite+aiosqlite:///{temp_db}")
        try:
            async with store._conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='book_snapshots'"
            ) as cur:
                row = await cur.fetchone()
            
            assert row is not None, "book_snapshots table not found"
            schema = str(row["sql"]).upper()
            
            # Verify schema contains correct PRIMARY KEY definition
            assert "INTEGER PRIMARY KEY AUTOINCREMENT" in schema, (
                f"book_snapshots schema missing 'INTEGER PRIMARY KEY AUTOINCREMENT'. "
                f"Got schema: {row['sql']}"
            )
        finally:
            await store.close()

    async def test_insert_always_gets_non_null_id(self, temp_store: Store) -> None:
        """INSERT baris baru harus selalu dapat id non-NULL & auto-increment."""
        # Insert 10 book snapshots
        base_ts = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)
        
        for i in range(10):
            book = OrderBook(
                token_id=f"token_{i % 2}",  # 2 tokens
                ts=base_ts.replace(second=i),
                bids=[BookLevel(price=Decimal("0.52"), size=Decimal(f"{100 + i}"))],
                asks=[BookLevel(price=Decimal("0.55"), size=Decimal(f"{80 + i}"))],
            )
            await temp_store.insert_book_snapshot(12345, book, mode="readonly")
        
        # Query all rows and verify:
        # 1. No NULL ids
        # 2. All ids are sequential & unique
        async with temp_store._conn.execute(
            "SELECT id, round_no, token_id FROM book_snapshots ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        
        assert len(rows) == 10, f"Expected 10 rows, got {len(rows)}"
        
        ids = [row["id"] for row in rows]
        
        # Verify no NULL ids
        assert all(id is not None for id in ids), (
            f"Found NULL id(s)! ids={ids}"
        )
        
        # Verify sequential (1, 2, 3, ..., 10)
        assert ids == list(range(1, 11)), (
            f"IDs not sequential! Expected [1..10], got {ids}"
        )

    async def test_dedup_preserves_rounds_and_pk(self, temp_db: str) -> None:
        """Prosedur de-dup tidak boleh menghapus ronde & merusak PK.
        
        Simulates the scenario where duplicate rows exist and need cleanup.
        Verifies:
        1. Round count preserved after de-dup
        2. PRIMARY KEY still works after de-dup
        3. No NULL ids introduced by de-dup
        """
        store = await Store.open(f"sqlite+aiosqlite:///{temp_db}")
        
        try:
            # SETUP: Insert data for 3 rounds with intentional duplicates
            base_ts = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)
            
            # Round 1: 5 unique snapshots
            for i in range(5):
                book = OrderBook(
                    token_id="token_up",
                    ts=base_ts.replace(second=i),
                    bids=[BookLevel(price=Decimal("0.52"), size=Decimal(f"{100 + i}"))],
                    asks=[BookLevel(price=Decimal("0.55"), size=Decimal(f"{80 + i}"))],
                )
                await store.insert_book_snapshot(1000, book, mode="readonly")
            
            # Round 2: 3 unique snapshots + 2 duplicates (same token_id/ts/prices)
            for i in range(3):
                book = OrderBook(
                    token_id="token_down",
                    ts=base_ts.replace(second=i * 2),
                    bids=[BookLevel(price=Decimal("0.48"), size=Decimal(f"{90 + i}"))],
                    asks=[BookLevel(price=Decimal("0.51"), size=Decimal(f"{70 + i}"))],
                )
                await store.insert_book_snapshot(2000, book, mode="readonly")
            
            # Insert 2 EXACT duplicates of first snapshot from round 2
            # (same token_id, ts, best_bid, best_ask, depths)
            dup_book = OrderBook(
                token_id="token_down",
                ts=base_ts.replace(second=0),
                bids=[BookLevel(price=Decimal("0.48"), size=Decimal("90"))],
                asks=[BookLevel(price=Decimal("0.51"), size=Decimal("70"))],
            )
            await store.insert_book_snapshot(2000, dup_book, mode="readonly")
            await store.insert_book_snapshot(2000, dup_book, mode="readonly")
            
            # Round 3: 4 unique snapshots
            for i in range(4):
                book = OrderBook(
                    token_id="token_up",
                    ts=base_ts.replace(second=i * 3),
                    bids=[BookLevel(price=Decimal("0.53"), size=Decimal(f"{110 + i}"))],
                    asks=[BookLevel(price=Decimal("0.56"), size=Decimal(f"{85 + i}"))],
                )
                await store.insert_book_snapshot(3000, book, mode="readonly")
            
            # PRE-CHECK: Count rounds & rows BEFORE de-dup
            async with store._conn.execute(
                "SELECT COUNT(DISTINCT round_no) as rounds, COUNT(*) as total "
                "FROM book_snapshots"
            ) as cur:
                pre_stats = await cur.fetchone()
            
            rounds_before = int(pre_stats["rounds"])
            total_before = int(pre_stats["total"])
            
            assert rounds_before == 3, f"Expected 3 distinct rounds, got {rounds_before}"
            assert total_before == 14, f"Expected 14 total rows (5+5+4), got {total_before}"
            
            # DE-DUP PROCEDURE (CORRECT WAY - not CREATE TABLE AS SELECT)
            # Remove duplicates while preserving one instance of each unique record
            await store._conn.execute("""
                DELETE FROM book_snapshots
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM book_snapshots
                    GROUP BY round_no, token_id, ts, best_bid, best_ask, 
                             bid_depth, ask_depth, gap, mode
                )
            """)
            await store._conn.commit()
            
            # POST-CHECK: Verify de-dup preserved rounds and PK
            async with store._conn.execute(
                "SELECT COUNT(DISTINCT round_no) as rounds, COUNT(*) as total, "
                "COUNT(CASE WHEN id IS NULL THEN 1 END) as null_ids, "
                "MIN(id) as min_id, MAX(id) as max_id "
                "FROM book_snapshots"
            ) as cur:
                post_stats = await cur.fetchone()
            
            rounds_after = int(post_stats["rounds"])
            total_after = int(post_stats["total"])
            null_ids = int(post_stats["null_ids"])
            min_id = post_stats["min_id"]
            max_id = post_stats["max_id"]
            
            # CRITICAL ASSERTIONS
            assert rounds_after == rounds_before, (
                f"De-dup changed round count! "
                f"BEFORE: {rounds_before}, AFTER: {rounds_after}"
            )
            
            assert null_ids == 0, (
                f"De-dup introduced NULL ids! Found {null_ids} NULL ids"
            )
            
            assert total_after == 12, (
                f"Expected 12 rows after de-dup (removed 2 duplicates), "
                f"got {total_after}"
            )
            
            assert min_id is not None and max_id is not None, (
                "min_id or max_id is NULL after de-dup!"
            )
            
            # Verify PRIMARY KEY still works: insert new row and check it gets valid id
            new_book = OrderBook(
                token_id="token_new",
                ts=base_ts.replace(second=99),
                bids=[BookLevel(price=Decimal("0.60"), size=Decimal("200"))],
                asks=[BookLevel(price=Decimal("0.62"), size=Decimal("180"))],
            )
            await store.insert_book_snapshot(4000, new_book, mode="readonly")
            
            # Check new row got valid id > max_id
            async with store._conn.execute(
                "SELECT id FROM book_snapshots ORDER BY id DESC LIMIT 1"
            ) as cur:
                last_row = await cur.fetchone()
            
            new_id = last_row["id"]
            assert new_id is not None, "New insert got NULL id!"
            assert new_id > max_id, (
                f"New insert id ({new_id}) not greater than previous max ({max_id}). "
                "AUTOINCREMENT broken!"
            )
            
        finally:
            await store.close()

    async def test_create_table_as_select_breaks_pk(self, temp_db: str) -> None:
        """Demonstrate that CREATE TABLE AS SELECT breaks PRIMARY KEY.
        
        This test shows WHY the bug happened: using CREATE TABLE AS SELECT
        loses the PRIMARY KEY AUTOINCREMENT constraint, causing NULL ids.
        
        This is a NEGATIVE test - we verify that CREATE TABLE AS SELECT is BAD.
        """
        store = await Store.open(f"sqlite+aiosqlite:///{temp_db}")
        
        try:
            # Insert some data
            base_ts = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)
            for i in range(3):
                book = OrderBook(
                    token_id="token_test",
                    ts=base_ts.replace(second=i),
                    bids=[BookLevel(price=Decimal("0.52"), size=Decimal(f"{100 + i}"))],
                    asks=[BookLevel(price=Decimal("0.55"), size=Decimal(f"{80 + i}"))],
                )
                await store.insert_book_snapshot(5000, book, mode="readonly")
            
            # Simulate BAD de-dup: CREATE TABLE AS SELECT (THIS IS WRONG!)
            await store._conn.execute("""
                CREATE TABLE book_snapshots_bad AS 
                SELECT * FROM book_snapshots
            """)
            await store._conn.commit()
            
            # Check schema of bad table - it will be missing PRIMARY KEY AUTOINCREMENT
            async with store._conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='book_snapshots_bad'"
            ) as cur:
                row = await cur.fetchone()
            
            bad_schema = str(row["sql"]).upper()
            
            # Verify BAD schema does NOT have proper PRIMARY KEY
            assert "PRIMARY KEY AUTOINCREMENT" not in bad_schema, (
                "CREATE TABLE AS SELECT should NOT preserve PRIMARY KEY AUTOINCREMENT"
            )
            
            # Try to insert into bad table (simulating what happens in production)
            # We expect id to be NULL or corrupt
            await store._conn.execute("""
                INSERT INTO book_snapshots_bad (
                    round_no, token_id, ts, best_bid, best_ask, 
                    bid_depth, ask_depth, gap, mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                6000, "token_bad", base_ts.replace(second=99).isoformat(),
                "0.50", "0.52", "100", "80", 0, "readonly"
            ))
            await store._conn.commit()
            
            # Check if new row has NULL id (it should!)
            async with store._conn.execute(
                "SELECT id FROM book_snapshots_bad WHERE round_no = 6000"
            ) as cur:
                bad_row = await cur.fetchone()
            
            # This demonstrates the bug: id is NULL!
            assert bad_row["id"] is None, (
                "Expected NULL id after CREATE TABLE AS SELECT + INSERT, "
                f"but got id={bad_row['id']}. Test assumptions wrong?"
            )
            
        finally:
            await store.close()


class TestBookSnapshotsIntegration:
    """Integration test: end-to-end insert/query flow."""

    async def test_realistic_workflow(self, temp_store: Store) -> None:
        """Test realistic workflow: insert snapshots, query, verify ids."""
        # Simulate recording 3 rounds with multiple snapshots each
        base_ts = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)
        
        rounds = [
            (1000, "token_up_1", "token_down_1", 5),
            (2000, "token_up_2", "token_down_2", 8),
            (3000, "token_up_3", "token_down_3", 3),
        ]
        
        expected_total = sum(count * 2 for _, _, _, count in rounds)  # 2 tokens per round
        
        for round_no, token_up, token_down, count in rounds:
            for i in range(count):
                ts = base_ts.replace(minute=round_no // 1000, second=i)
                
                # Insert for UP token
                book_up = OrderBook(
                    token_id=token_up,
                    ts=ts,
                    bids=[BookLevel(price=Decimal("0.52"), size=Decimal(f"{100 + i}"))],
                    asks=[BookLevel(price=Decimal("0.55"), size=Decimal(f"{80 + i}"))],
                )
                await temp_store.insert_book_snapshot(round_no, book_up, mode="readonly")
                
                # Insert for DOWN token
                book_down = OrderBook(
                    token_id=token_down,
                    ts=ts,
                    bids=[BookLevel(price=Decimal("0.48"), size=Decimal(f"{90 + i}"))],
                    asks=[BookLevel(price=Decimal("0.51"), size=Decimal(f"{70 + i}"))],
                )
                await temp_store.insert_book_snapshot(round_no, book_down, mode="readonly")
        
        # Query and verify
        async with temp_store._conn.execute(
            "SELECT COUNT(*) as total, "
            "COUNT(DISTINCT round_no) as rounds, "
            "COUNT(CASE WHEN id IS NULL THEN 1 END) as null_ids, "
            "MIN(id) as min_id, MAX(id) as max_id "
            "FROM book_snapshots"
        ) as cur:
            stats = await cur.fetchone()
        
        total = int(stats["total"])
        distinct_rounds = int(stats["rounds"])
        null_ids = int(stats["null_ids"])
        min_id = int(stats["min_id"])
        max_id = int(stats["max_id"])
        
        assert total == expected_total, f"Expected {expected_total} rows, got {total}"
        assert distinct_rounds == 3, f"Expected 3 rounds, got {distinct_rounds}"
        assert null_ids == 0, f"Found {null_ids} NULL ids!"
        assert min_id == 1, f"Expected min_id=1, got {min_id}"
        assert max_id == expected_total, f"Expected max_id={expected_total}, got {max_id}"
        
        # Verify we can query by round_no
        for round_no, _, _, count in rounds:
            snapshots = await temp_store.get_book_snapshots(round_no)
            assert len(snapshots) == count * 2, (
                f"Round {round_no}: expected {count * 2} snapshots, got {len(snapshots)}"
            )
