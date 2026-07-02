# Duplicate book_snapshots Investigation — Runtime Evidence Collection

## Objective

Collect runtime evidence to prove or disprove hypotheses about why identical rows appear in the `book_snapshots` table.

**DO NOT** implement fixes until root cause is proven by runtime logs.

---

## Problem Statement

Database contains duplicate rows with:
- Same `token_id`, `ts`, `best_bid`, `best_ask`, `bid_depth`, `ask_depth`
- Different primary keys (proving multiple INSERT operations occurred)
- Example: `dup=2` (one duplicate), `dup=3` (two duplicates)

---

## Hypotheses (from Static Analysis)

1. **PRIMARY (85% confidence)**: Reconnect-triggered snapshot replay during finegrain window
   - `_read_market()` creates fresh `BookState()` per connection
   - Every reconnect → `_subscribe()` → server re-sends full snapshot
   - Server assigns identical `timestamp` for same market state
   - `book_finegrain_sec=45` disables throttle in last 45s
   - Quiet markets → stale timeout → reconnect → snapshot replay

2. **SECONDARY**: Multiple `price_change` entries for same asset with identical timestamp

3. **SECONDARY**: Closing-snapshot path writes duplicate

4. **SECONDARY**: Stale timeout fires during finegrain mode

---

## Instrumentation Added

### 1. WebSocket Lifecycle (`src/btcbot/adapters/clob_ws.py`)

**Location**: `HttpClobWS.stream_market()`

**Logs**:
```python
log.info(
    "ws_lifecycle",
    lifecycle_event="CONNECTED" | "RECONNECTED",
    attempt=attempts,
    token_ids=token_ids,
    wall_time=clock.now().isoformat(),
)

log.warning(
    "ws_connect_failed",
    attempt=attempts,
    error=str(exc),
    error_type=type(exc).__name__,
)

log.warning(
    "ws_disconnected",
    error=str(exc),
    error_type=type(exc).__name__,
)

log.info("ws_subscribe", token_ids=token_ids)

log.info("ws_reconnect_backoff", backoff=backoff, attempt=attempts)

log.info("ws_gave_up", total_attempts=attempts)
```

### 2. Raw WebSocket Frames (`src/btcbot/adapters/clob_ws.py`)

**Location**: `HttpClobWS._read_market()` — BEFORE parsing

**Logs**:
```python
log.info(
    "ws_frame_received",
    element_idx=i,
    event_type=event_type,
    is_snapshot=is_snapshot,
    is_price_change=is_price_change,
    timestamp_raw=timestamp_raw,
    asset_ids=asset_ids,
    num_price_changes=len(price_changes) if is_price_change else 0,
)
```

### 3. Stale Timeout (`src/btcbot/adapters/clob_ws.py`)

**Location**: `HttpClobWS._read_market()`

**Logs**:
```python
log.warning(
    "ws_stale_timeout",
    stale_sec=stale_sec,
    wall_time=clock.now().isoformat(),
)
```

### 4. Parser Output (`src/btcbot/adapters/clob_ws.py`)

**Location**: `HttpClobWS._read_market()` — AFTER `parse_ws_element()`

**Logs**:
```python
log.info(
    "ws_parser_output",
    token_id=book.token_id,
    ts=book.ts.isoformat(),
    best_bid=str(best_bid),
    best_ask=str(best_ask),
    bid_depth=str(bid_depth),
    ask_depth=str(ask_depth),
)
```

### 5. Recorder Input (`src/btcbot/data/recorder.py`)

**Location**: `Recorder.consume_market()` — BEFORE `_should_persist()`

**Logs**:
```python
log.info(
    "recorder_book_received",
    round_no=round_no,
    token_id=book.token_id,
    ts=book.ts.isoformat(),
    best_bid=str(best_bid),
    best_ask=str(best_ask),
    bid_depth=str(bid_depth),
    ask_depth=str(ask_depth),
)
```

### 6. Persistence Decision (`src/btcbot/data/recorder.py`)

**Location**: `Recorder._should_persist()`

**Logs**:
```python
log.info(
    "persist_decision",
    token_id=token_id,
    ts=book.ts.isoformat(),
    decision=True | False,
    reason="persist_mode_all" | "first_snapshot" | "price_changed" | 
           "finegrain_mode" | "throttle_expired" | "throttle_active",
    # additional fields depending on reason
)
```

### 7. Persistence Execution (`src/btcbot/data/recorder.py`)

**Location**: `Recorder._persist_book()`

**Logs**:
```python
log.info(
    "persist_book",
    round_no=round_no,
    token_id=token_id,
    ts=book.ts.isoformat(),
    best_bid=str(best_bid),
    best_ask=str(best_ask),
    bid_depth=str(bid_depth),
    ask_depth=str(ask_depth),
    mode=mode,
)
```

---

## How to Collect Evidence

### Step 1: Enable Structured Logging

Ensure `structlog` is configured to emit JSON logs with all fields.

**Check current config** (should already be present):
```python
# In src/btcbot/config/settings.py or app startup
import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer()
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)
```

### Step 2: Run Recorder with Instrumentation

Run the recorder for at least one complete round (preferably multiple rounds to capture reconnects):

```powershell
# Example using demo/price_sampler (adjust based on your CLI)
python -m btcbot.app.cli record --round-no 999 --duration 300
```

**OR** if you have a specific recording script:
```powershell
python scripts/your_recorder_script.py
```

**Redirect logs to file**:
```powershell
python -m btcbot.app.cli record --round-no 999 2>&1 | Tee-Object -FilePath duplicate_investigation.log
```

### Step 3: Let It Run During High-Risk Periods

Based on hypothesis #1, duplicates are most likely when:
- **Last 45 seconds of round** (`book_finegrain_sec=45`)
- **Quiet market** (few price changes → stale timeout)
- **Reconnect occurs during finegrain mode**

**Ideal test**:
- Start recording at beginning of round
- Let it run through window close + drain period
- Capture at least one reconnect event

### Step 4: Query for Duplicates

After recording completes, query SQLite for duplicates:

```sql
-- Find duplicate rows
SELECT 
    token_id, 
    ts, 
    best_bid, 
    best_ask, 
    bid_depth, 
    ask_depth, 
    COUNT(*) AS dup,
    GROUP_CONCAT(id) AS row_ids
FROM book_snapshots 
WHERE round_no = 999  -- adjust to your round
GROUP BY token_id, ts, best_bid, best_ask, bid_depth, ask_depth 
HAVING dup > 1
ORDER BY ts, token_id;
```

**Pick ONE duplicate** for detailed analysis:
```
token_id = 12345...
ts = 2026-06-29T12:34:56.789000+00:00
best_bid = 0.52
best_ask = 0.53
```

### Step 5: Extract Timeline from Logs

Search logs for that specific `token_id` and `ts`:

```powershell
# Extract all events for the duplicate
Get-Content duplicate_investigation.log | Select-String '"token_id":"12345..."' | Select-String '"ts":"2026-06-29T12:34:56.789'
```

**OR** using Python:
```python
import json

target_token = "12345..."
target_ts = "2026-06-29T12:34:56.789"

with open("duplicate_investigation.log") as f:
    for line in f:
        try:
            log = json.loads(line)
            if log.get("token_id") == target_token and target_ts in log.get("ts", ""):
                print(json.dumps(log, indent=2))
        except:
            pass
```

---

## Analysis Checklist

For the chosen duplicate, answer these questions using log evidence:

### A. WebSocket Layer

- [ ] Did a `ws_lifecycle` event (RECONNECTED) occur near the duplicate timestamp?
- [ ] What was the `wall_time` of the reconnect?
- [ ] What was the reconnect `attempt` number?
- [ ] Did `ws_subscribe` get called after the reconnect?

### B. Raw Frame Layer

- [ ] How many `ws_frame_received` events have `is_snapshot=true` for this `token_id`?
- [ ] Do multiple frames have identical `timestamp_raw`?
- [ ] Are there multiple `ws_frame_received` events with same `asset_ids` and `timestamp_raw`?

### C. Parser Layer

- [ ] How many `ws_parser_output` events have this exact `ts` and `token_id`?
- [ ] Do multiple parser outputs have identical `best_bid`, `best_ask`, `bid_depth`, `ask_depth`?
- [ ] Were these parser outputs from the SAME frame or DIFFERENT frames?

### D. Recorder Layer

- [ ] How many `recorder_book_received` events have this exact `ts` and `token_id`?
- [ ] Are the `best_bid`/`best_ask` values identical across all received events?

### E. Persistence Decision Layer

- [ ] How many `persist_decision` events have `decision=true` for this `ts` and `token_id`?
- [ ] What are the `reason` values?
  - [ ] `first_snapshot`
  - [ ] `price_changed`
  - [ ] `finegrain_mode`
  - [ ] `throttle_expired`
- [ ] Did `first_snapshot` reason appear MORE THAN ONCE? (smoking gun for reconnect hypothesis)

### F. Persistence Execution Layer

- [ ] How many `persist_book` events occurred for this `ts` and `token_id`?
- [ ] Are the persisted values identical?

---

## Expected Evidence Patterns

### Pattern 1: Reconnect Replay (PRIMARY HYPOTHESIS)

**Timeline**:
```
12:34:50  ws_lifecycle: lifecycle_event=CONNECTED, attempt=0
12:34:51  ws_subscribe: token_ids=[UP, DOWN]
12:34:52  ws_frame_received: is_snapshot=true, token_id=UP, timestamp=12:34:52
12:34:52  ws_parser_output: token_id=UP, ts=12:34:52, bid=0.52
12:34:52  recorder_book_received: token_id=UP, ts=12:34:52, bid=0.52
12:34:52  persist_decision: decision=true, reason=first_snapshot
12:34:52  persist_book: token_id=UP, ts=12:34:52, bid=0.52  ← INSERT #1
...
12:35:30  ws_stale_timeout: stale_sec=30  ← quiet market
12:35:30  ws_disconnected
12:35:31  ws_lifecycle: lifecycle_event=RECONNECTED, attempt=1  ← RECONNECT
12:35:31  ws_subscribe: token_ids=[UP, DOWN]  ← RE-SUBSCRIBE
12:35:32  ws_frame_received: is_snapshot=true, token_id=UP, timestamp=12:34:52  ← SAME TS!
12:35:32  ws_parser_output: token_id=UP, ts=12:34:52, bid=0.52  ← IDENTICAL BOOK
12:35:32  recorder_book_received: token_id=UP, ts=12:34:52, bid=0.52
12:35:32  persist_decision: decision=true, reason=first_snapshot  ← NEW BookState thinks it's first!
12:35:32  persist_book: token_id=UP, ts=12:34:52, bid=0.52  ← INSERT #2 (DUPLICATE!)
```

**Key Evidence**:
- `RECONNECTED` event between two `persist_book` calls with same `ts`
- `reason=first_snapshot` appears TWICE for same `token_id` (different BookState instances)
- Server `timestamp` in snapshot is OLDER than wall-clock (stale market state)

### Pattern 2: Duplicate Server Frame

**Timeline**:
```
12:34:52  ws_frame_received: is_snapshot=true, token_id=UP, timestamp=12:34:52
12:34:52  ws_parser_output: token_id=UP, ts=12:34:52, bid=0.52
12:34:52  persist_book: token_id=UP, ts=12:34:52, bid=0.52  ← INSERT #1
12:34:52  ws_frame_received: is_snapshot=true, token_id=UP, timestamp=12:34:52  ← DUPLICATE FRAME!
12:34:52  ws_parser_output: token_id=UP, ts=12:34:52, bid=0.52
12:34:52  persist_decision: decision=true, reason=price_changed  ← or finegrain_mode
12:34:52  persist_book: token_id=UP, ts=12:34:52, bid=0.52  ← INSERT #2
```

**Key Evidence**:
- Two `ws_frame_received` with identical `timestamp_raw` and `asset_ids`
- NO `RECONNECTED` event between them
- Both frames occur within same connection

### Pattern 3: Multiple price_change Entries

**Timeline**:
```
12:34:52  ws_frame_received: event_type=price_change, num_price_changes=2, asset_ids=[UP, UP]
12:34:52  ws_parser_output: token_id=UP, ts=12:34:52, bid=0.52  ← first entry
12:34:52  ws_parser_output: token_id=UP, ts=12:34:52, bid=0.52  ← second entry (SAME ASSET!)
12:34:52  recorder_book_received: token_id=UP, ts=12:34:52, bid=0.52  ← first
12:34:52  persist_book: token_id=UP, ts=12:34:52, bid=0.52  ← INSERT #1
12:34:52  recorder_book_received: token_id=UP, ts=12:34:52, bid=0.52  ← second
12:34:52  persist_decision: decision=true, reason=finegrain_mode
12:34:52  persist_book: token_id=UP, ts=12:34:52, bid=0.52  ← INSERT #2
```

**Key Evidence**:
- ONE `ws_frame_received` with `num_price_changes > 1`
- Multiple `asset_ids` containing same token
- Multiple `ws_parser_output` from SAME frame

---

## Deliverable: Evidence Report

Create `DUPLICATE_EVIDENCE_REPORT.md` with:

### Section A: Duplicate Example

```
token_id: 12345...
timestamp: 2026-06-29T12:34:56.789000+00:00
best_bid: 0.52
best_ask: 0.53
dup_count: 2
row_ids: [1234, 1235]
round_no: 999
```

### Section B: Complete Timeline

```
[Extracted logs chronologically showing:]
- All ws_lifecycle events
- All ws_frame_received for this token_id
- All ws_parser_output for this ts
- All recorder_book_received for this ts
- All persist_decision for this ts
- All persist_book for this ts
```

### Section C: Hypothesis Evaluation

| Hypothesis | Supported? | Evidence |
|------------|------------|----------|
| Reconnect replay | YES/NO | [specific log entries] |
| Duplicate server frame | YES/NO | [specific log entries] |
| Duplicate parser output | YES/NO | [specific log entries] |
| Duplicate recorder input | YES/NO | [specific log entries] |
| Persistence bug | YES/NO | [specific log entries] |

### Section D: Root Cause Conclusion

**Supported by runtime evidence:**

[State the root cause with confidence level and cite specific log entries as proof]

---

## Next Steps After Evidence Collection

1. **IF** reconnect replay is proven → implement deduplication at BookState level (tie BookState to round, not connection)
2. **IF** duplicate server frames proven → add frame deduplication at receive level
3. **IF** multiple price_change entries proven → fix parser to deduplicate per asset
4. **IF** persistence bug proven → fix Recorder logic
5. **IF** none proven → expand investigation scope

**DO NOT** implement fixes until evidence report is complete and reviewed.

---

## Files Modified (Instrumentation Only)

- `src/btcbot/adapters/clob_ws.py` — lifecycle, frame, parser logging
- `src/btcbot/data/recorder.py` — recorder input, decision, persistence logging

**NO BEHAVIOR CHANGES** — pure observability.
