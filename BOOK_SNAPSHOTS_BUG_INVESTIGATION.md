# Book Snapshots Recording Stopped - Investigation Report

**Date**: 2026-07-06  
**Status**: Code Review Complete - No Bug Found in `_should_persist()` Logic  
**Production Evidence**: book_snapshots stopped at Jul 4 23:32, signals continue normally

## Summary

Investigation into why `book_snapshots` stopped recording while `signals` continued. Initial hypothesis was that the logging reduction commit (b13ad26) broke `_should_persist()` logic when `instrumentation_verbose=False`. **Code review shows this is NOT the case** - all return statements are correctly placed outside the conditional logging blocks.

## Code Review Findings

### `_should_persist()` Method (lines 305-391)

**Structure is CORRECT**:
```python
def _should_persist(...) -> bool:
    if self._persist_mode == "all":
        if self._instrumentation_verbose:  # Line 318
            log.info(...)  # Lines 319-327
        return True  # Line 329 - CORRECT: outside if block

    last = self._last_persist.get(book.token_id)
    if last is None:
        if self._instrumentation_verbose:  # Line 333
            log.info(...)  # Lines 334-341
        return True  # Line 342 - CORRECT: outside if block

    best_bid, best_ask = _best(book)
    last_bid, last_ask, last_ms = last

    if best_bid != last_bid or best_ask != last_ask:
        if self._instrumentation_verbose:  # Line 349
            log.info(...)  # Lines 350-360
        return True  # Line 361 - CORRECT: outside if block

    if window_end is not None and (window_end - now).total_seconds() <= self._finegrain_sec:
        if self._instrumentation_verbose:  # Line 365
            log.info(...)  # Lines 366-374
        return True  # Line 374 - CORRECT: outside if block

    now_ms = int(now.timestamp() * 1000)
    throttle_elapsed = (now_ms - last_ms) >= self._sample_ms

    if self._instrumentation_verbose:  # Line 379
        log.info(...)  # Lines 380-390
    
    return throttle_elapsed  # Line 391 - CORRECT: outside if block
```

**Verdict**: All return statements are at the correct indentation level. The `instrumentation_verbose` flag ONLY controls logging, NOT the return logic.

### `_persist_book()` Method (lines 393-413)

**Structure is CORRECT**:
```python
async def _persist_book(self, round_no: int, book: OrderBook, now: datetime) -> None:
    best_bid, best_ask = _best(book)
    log.info(
        "persist_book",  # This is INFO level, NOT gated
        ...
    )
    
    await self._store.insert_book_snapshot(round_no, book, mode=self._mode)
    self._last_persist[book.token_id] = (best_bid, best_ask, int(now.timestamp() * 1000))
```

**Verdict**: The `insert_book_snapshot()` call is unconditional and should always execute when `_persist_book()` is called.

### `consume_market()` Method (lines 178-287)

**Flow is CORRECT**:
```python
if self._should_persist(round_no, book, window_end, now):
    await self._persist_book(round_no, book, now)
    persisted_latest[book.token_id] = True
    written += 1
else:
    persisted_latest[book.token_id] = False
```

**Verdict**: If `_should_persist()` returns `True`, `_persist_book()` WILL be called. No conditional logic that would skip it based on `instrumentation_verbose`.

## Alternative Root Causes

Since the code logic is correct, the production issue must have a different cause:

### 1. WebSocket Connection Issue
- **Hypothesis**: `stream_market()` stopped yielding OrderBook objects
- **Evidence needed**: Check production logs for:
  - `ws_disconnect` events around Jul 4 23:32
  - `ws_reconnect` attempts
  - `heartbeat` logs showing `consumed=0` after Jul 4 23:32
- **How to verify**: Look for `recorder_book_received` logs (added at line 257)

### 2. Market Discovery Changed
- **Hypothesis**: Bot stopped finding markets with valid orderbooks
- **Evidence needed**: Check if rounds after Jul 4 23:32 have:
  - Valid `token_id_up` and `token_id_down`
  - Markets that are actually active on CLOB
- **How to verify**: Query `rounds` table for rounds > 1783208100

### 3. CLOB API Changes
- **Hypothesis**: Polymarket CLOB API changed after Jul 4, breaking WebSocket
- **Evidence needed**: Check Polymarket changelogs or API status
- **How to verify**: Test WebSocket connection manually with current API

### 4. Process Restart with Different Config
- **Hypothesis**: Bot restarted with different settings (updates_per_round=0?)
- **Evidence needed**: Check process logs around Jul 4 23:32
- **How to verify**: Check `.env` file on VPS for any unexpected values

### 5. Database Write Permissions
- **Hypothesis**: DB permissions changed, affecting `book_snapshots` table only
- **Evidence needed**: Check DB file permissions
- **How to verify**: Try manual INSERT into `book_snapshots` table

## Regression Test Added

Added `TestInstrumentationVerboseRegression` class to `tests/data/test_recorder.py` with 3 tests:
1. `test_persist_with_instrumentation_verbose_false` - Verifies basic INSERT works
2. `test_persist_mode_changes_with_verbose_false` - Verifies throttle logic works  
3. `test_persist_mode_all_with_verbose_false` - Verifies mode='all' works

**Expected Result**: All tests should PASS, confirming the code is correct.

## Recommended Next Steps

1. **On VPS, check production logs** around Jul 4 23:32:
   ```bash
   grep "2026-07-04T23:3" /path/to/logs | grep -E "(disconnect|reconnect|heartbeat|recorder_book_received)"
   ```

2. **Check if bot is actually calling consume_market**:
   ```bash
   grep "heartbeat" /path/to/logs | tail -20
   ```
   - If `consumed=0` consistently, WebSocket is not delivering books

3. **Verify DB table is writable**:
   ```sql
   INSERT INTO book_snapshots (round_no, token_id, ts, gap, mode) 
   VALUES (99999, 'test', datetime('now'), 0, 'readonly');
   SELECT * FROM book_snapshots WHERE round_no = 99999;
   DELETE FROM book_snapshots WHERE round_no = 99999;
   ```

4. **Test WebSocket manually**:
   ```python
   # Run this on VPS to test if CLOB WebSocket is working
   python -c "
   import asyncio
   from btcbot.adapters.clob_ws import HttpClobWS
   async def test():
       ws = HttpClobWS('wss://ws-subscriptions-clob.polymarket.com/ws/market')
       stream = ws.stream_market(['test_token'])
       try:
           book = await asyncio.wait_for(stream.__anext__(), timeout=30.0)
           print(f'SUCCESS: Got book {book}')
       except Exception as e:
           print(f'FAILED: {e}')
   asyncio.run(test())
   "
   ```

5. **If tests cannot run** due to Application Control policy:
   - Request user to run tests manually on VPS: `uv run pytest tests/data/test_recorder.py::TestInstrumentationVerboseRegression -v`
   - Or run diagnostic script: `uv run python test_instrumentation_manual.py`

## Conclusion

The logging reduction commit (b13ad26) did NOT introduce a bug in the `_should_persist()` or `_persist_book()` logic. The code correctly separates logging (conditional on `instrumentation_verbose`) from business logic (unconditional).

The production issue where `book_snapshots` stopped recording must have a different root cause, likely related to:
- WebSocket connection failure
- Market discovery changes
- External API changes
- Process/configuration changes

**Recommendation**: Focus investigation on WebSocket logs and connectivity rather than the recorder logic.
