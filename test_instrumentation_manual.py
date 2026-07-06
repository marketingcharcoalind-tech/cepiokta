"""Manual diagnostic script to test instrumentation_verbose=False behavior."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.adapters.chainlink import FakePriceSource
from btcbot.adapters.clob_ws import HttpClobWS
from btcbot.adapters.clock import SimClock
from btcbot.data.recorder import Recorder
from btcbot.data.store import Store
from btcbot.domain.models import BookLevel, OrderBook


async def main() -> None:
    """Test that with instrumentation_verbose=False, book_snapshots are still inserted."""
    
    # Setup
    store = await Store.open("sqlite+aiosqlite:///:memory:")
    clock = SimClock(datetime(2026, 6, 25, 10, 0, tzinfo=UTC))
    feed = FakePriceSource(Decimal("64000"))
    ws = HttpClobWS("wss://test")
    
    # Create recorder with instrumentation_verbose=False (default)
    recorder = Recorder(
        store,
        ws,
        feed,
        clock,
        mode="readonly",
        instrumentation_verbose=False,  # CRITICAL: this is the default
    )
    
    print(f"Recorder created with instrumentation_verbose={recorder._instrumentation_verbose}")
    
    # Create a simple book
    book = OrderBook(
        token_id="test_token",
        ts=clock.now(),
        bids=[BookLevel(price=Decimal("0.52"), size=Decimal("100"))],
        asks=[BookLevel(price=Decimal("0.55"), size=Decimal("80"))],
    )
    
    # Test _should_persist with first book (should return True)
    round_no = 12345
    should_persist = recorder._should_persist(round_no, book, None, clock.now())
    print(f"First book _should_persist returned: {should_persist}")
    
    if should_persist:
        # Call _persist_book
        await recorder._persist_book(round_no, book, clock.now())
        print("Called _persist_book")
        
        # Check if it was actually inserted
        snapshots = await store.get_book_snapshots(round_no)
        print(f"Number of snapshots in DB: {len(snapshots)}")
        
        if len(snapshots) > 0:
            snap = snapshots[0]
            print(f"✅ SUCCESS: Book snapshot inserted!")
            print(f"   token_id: {snap.token_id}")
            print(f"   best_bid: {snap.best_bid}")
            print(f"   best_ask: {snap.best_ask}")
        else:
            print("❌ FAILURE: No snapshots found in DB!")
    else:
        print("❌ FAILURE: _should_persist returned False for first book!")
    
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
