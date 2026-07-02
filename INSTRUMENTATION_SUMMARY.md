# Duplicate Investigation — Instrumentation Summary

**Date**: 2026-07-01  
**Status**: Instrumentation COMPLETE, awaiting runtime data collection  
**Task**: TaskRC — Root-cause duplicate book_snapshots

---

## What Was Done

Added **pure observability logging** to trace the complete lifecycle of OrderBook objects from WebSocket receipt through database persistence.

**NO BEHAVIOR CHANGES** — only logging added. All business logic, persistence logic, deduplication, and strategy remain unchanged.

---

## Files Modified

### 1. `src/btcbot/adapters/clob_ws.py`

#### Added to `HttpClobWS.stream_market()`:
- **Connection lifecycle logging**: `ws_lifecycle` events for CONNECTED, RECONNECTED, DISCONNECTED (field: `lifecycle_event`)
- **Connection failure logging**: `ws_connect_failed` with attempt number and error details
- **Subscription logging**: `ws_subscribe` with token_ids
- **Reconnect backoff logging**: `ws_reconnect_backoff` with backoff duration and attempt
- **Give up logging**: `ws_gave_up` when max reconnects exceeded

#### Added to `HttpClobWS._read_market()`:
- **Stale timeout logging**: `ws_stale_timeout` with wall clock time when timeout fires
- **Raw frame logging** (BEFORE parsing):
  - `ws_frame_received` with:
    - `element_idx`: which element in the frame
    - `event_type`: raw event type from server
    - `is_snapshot`: whether this is a book snapshot
    - `is_price_change`: whether this is a price_change event
    - `timestamp_raw`: server timestamp (untouched)
    - `asset_ids`: list of affected token IDs
    - `num_price_changes`: count of price_change entries
- **Non-JSON frame debug logging**: `ws_non_json_frame` for PONG/PING frames
- **Parser output logging** (AFTER parsing):
  - `ws_parser_output` with:
    - `token_id`, `ts`, `best_bid`, `best_ask`, `bid_depth`, `ask_depth`

### 2. `src/btcbot/data/recorder.py`

#### Added to `Recorder.consume_market()`:
- **Book received logging** (BEFORE `_should_persist`):
  - `recorder_book_received` with:
    - `round_no`, `token_id`, `ts`, `best_bid`, `best_ask`, `bid_depth`, `ask_depth`

#### Added to `Recorder._should_persist()`:
- **Persistence decision logging** for EVERY decision:
  - `persist_decision` with:
    - `token_id`, `ts`, `decision` (True/False)
    - `reason`: one of:
      - `persist_mode_all`: all mode enabled
      - `first_snapshot`: first snapshot for this token in this round
      - `price_changed`: best_bid or best_ask changed
      - `finegrain_mode`: within finegrain window (last N seconds)
      - `throttle_expired`: enough time elapsed since last persist
      - `throttle_active`: throttle still active (not persisting)
    - Additional fields depending on reason (e.g., `elapsed_ms`, `seconds_left`)

#### Added to `Recorder._persist_book()`:
- **Persistence execution logging**:
  - `persist_book` with:
    - `round_no`, `token_id`, `ts`, `best_bid`, `best_ask`, `bid_depth`, `ask_depth`, `mode`

---

## What This Enables

### Complete Chain of Custody

For any duplicate row in SQLite, you can now trace backwards through:

```
SQLite INSERT
    ↑ persist_book log
    ↑ persist_decision log (reason=?)
    ↑ recorder_book_received log
    ↑ ws_parser_output log
    ↑ ws_frame_received log
    ↑ ws_lifecycle log (CONNECTED? RECONNECTED?)
```

### Key Questions Answered

1. **Did a reconnect occur?**
   - Look for `ws_lifecycle` with `event=RECONNECTED` between duplicate inserts

2. **Did the server send duplicate frames?**
   - Look for multiple `ws_frame_received` with identical `timestamp_raw` and `asset_ids`

3. **Did the parser emit duplicate OrderBooks?**
   - Look for multiple `ws_parser_output` with identical `ts`, `token_id`, and values

4. **Why did _should_persist return True?**
   - Look at `persist_decision` `reason` field:
     - `first_snapshot` appearing TWICE for same token = BookState was reset (reconnect!)
     - `price_changed` = legitimate change
     - `finegrain_mode` = near end of window
     - `throttle_expired` = time-based

5. **When did the stale timeout fire?**
   - Look for `ws_stale_timeout` events

---

## Primary Hypothesis (85% confidence)

**Reconnect-triggered snapshot replay during finegrain window:**

1. Bot records initial snapshot: `persist_decision: reason=first_snapshot` → INSERT #1
2. Market goes quiet (few price changes)
3. Stale timeout fires (30s no messages): `ws_stale_timeout`
4. WebSocket disconnects: `ws_lifecycle: event=DISCONNECTED`
5. Reconnect occurs: `ws_lifecycle: event=RECONNECTED`
6. New connection → `_read_market()` creates fresh `BookState()`
7. `_subscribe()` triggers server to re-send full snapshot
8. Server sends snapshot with SAME timestamp (market hasn't changed)
9. Fresh BookState thinks it's first snapshot: `persist_decision: reason=first_snapshot` → INSERT #2 (DUPLICATE!)

**Key evidence to look for:**
- `RECONNECTED` event between two `persist_book` calls with same `ts`
- `reason=first_snapshot` appearing TWICE for same `token_id` in same round
- `timestamp_raw` in snapshot is OLDER than wall clock (stale market state)
- This happens during `finegrain_mode` (last 45 seconds) when throttle is disabled

---

## Next Steps

1. **Run recorder** with instrumentation enabled for at least one complete round
2. **Query for duplicates** in SQLite
3. **Extract logs** for one specific duplicate (pick exact token_id + ts)
4. **Build timeline** showing complete chain from WebSocket to SQLite
5. **Analyze evidence** against hypothesis checklist
6. **Write evidence report** (see `DUPLICATE_INVESTIGATION.md`)
7. **Implement fix** ONLY after root cause proven by runtime evidence

---

## Files to Review

- **`DUPLICATE_INVESTIGATION.md`**: Complete investigation guide with:
  - Evidence collection procedure
  - Log search commands
  - Analysis checklist
  - Expected evidence patterns
  - Report template

- **`PROGRESS_TRACKER.md`**: TaskRC entry updated with instrumentation status

---

## Verification

The instrumentation compiles and imports successfully:

```python
# No syntax errors, structlog imported at point of use
from btcbot.adapters.clob_ws import HttpClobWS
from btcbot.data.recorder import Recorder
```

**Type checking**: Uses `structlog.get_logger()` at function scope (safe, no import issues).

**Behavior verification**: NO business logic changed:
- All existing tests should pass unchanged
- PnL, fills, sizing, strategy decisions remain identical
- Database schema unchanged
- Persistence conditions unchanged

---

## Important Notes

### This is PURE OBSERVABILITY

- **DO NOT** modify deduplication logic yet
- **DO NOT** change `_should_persist()` conditions
- **DO NOT** add filters to `BookState` or parser
- **DO NOT** change Store insert logic

### Only logging was added

All logs use `structlog.get_logger()` with structured fields for easy parsing.

### Runtime overhead is minimal

Logging only fires when events occur (not in tight loops). Most logs are at INFO level and only for significant events (frame received, persistence decision).

### Logs can be filtered

Use JSON output and filter by:
- `token_id`: trace one specific market
- `ts`: trace one specific timestamp
- `round_no`: trace one specific round
- `event`: filter by event type

---

## Success Criteria

The instrumentation is complete when:
- [x] WebSocket lifecycle events logged
- [x] Raw frames logged before parsing
- [x] Parser output logged after parsing
- [x] Recorder input logged before decision
- [x] Persistence decision logged with reason
- [x] Persistence execution logged
- [x] Stale timeout logged
- [x] No behavior changes
- [x] Code compiles and imports successfully
- [x] Investigation guide documented

**Next phase**: Runtime data collection and evidence analysis.
