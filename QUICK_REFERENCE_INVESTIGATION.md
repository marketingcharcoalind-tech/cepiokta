# Quick Reference — Duplicate Investigation

## TL;DR

Instrumentation is complete. Run recorder with logs enabled, find a duplicate, trace it backwards through the logs.

---

## Quick Commands

### 1. Run Recorder with Logging

```powershell
# Run for one round, capture logs to file
python -m btcbot.app.cli record --round-no 999 2>&1 | Tee-Object -FilePath investigation.log
```

### 2. Find Duplicates

```sql
-- Run in SQLite
SELECT 
    token_id, 
    ts, 
    best_bid, 
    best_ask, 
    COUNT(*) AS dup,
    GROUP_CONCAT(id) AS row_ids
FROM book_snapshots 
WHERE round_no = 999
GROUP BY token_id, ts, best_bid, best_ask
HAVING dup > 1
LIMIT 5;
```

### 3. Extract Timeline for One Duplicate

```powershell
# Replace with actual values from SQL query
$token = "12345..."
$ts = "2026-06-29T12:34:56.789"

Get-Content investigation.log | Select-String $token | Select-String $ts
```

### 4. Look for Smoking Gun

Search logs for this pattern:

```
ws_lifecycle: event=RECONNECTED  <-- Connection reset
persist_decision: reason=first_snapshot  <-- First time
persist_book: [values]
... (later, same token_id and ts)
persist_decision: reason=first_snapshot  <-- AGAIN! This is the bug!
persist_book: [same values]  <-- DUPLICATE INSERT
```

---

## Log Events to Look For

### Connection Events
- `ws_lifecycle`: CONNECTED, RECONNECTED, DISCONNECTED
- `ws_stale_timeout`: No messages for 30+ seconds
- `ws_reconnect_backoff`: Waiting before retry

### Data Flow Events
- `ws_frame_received`: Raw data from server
- `ws_parser_output`: Parsed OrderBook
- `recorder_book_received`: Received by Recorder
- `persist_decision`: Should we persist? (reason=?)
- `persist_book`: Actually persisted to DB

---

## Hypothesis to Prove

**Reconnect causes duplicate `first_snapshot`:**

- Fresh `BookState()` is created per connection (not per round)
- After reconnect, new BookState thinks it's seeing token for first time
- Server re-sends snapshot with SAME old timestamp
- `reason=first_snapshot` fires AGAIN
- Duplicate INSERT happens

**Evidence needed:**
1. RECONNECTED event between two persist_book calls with same ts
2. `reason=first_snapshot` appearing TWICE for same token_id
3. Server timestamp is older than wall clock time

---

## What NOT to Do

- ❌ Don't implement deduplication yet
- ❌ Don't modify _should_persist logic
- ❌ Don't change BookState
- ❌ Don't touch Store
- ✅ Only collect logs and analyze

---

## Expected Timeline

```
12:34:50  CONNECTED                          ← Initial connection
12:34:51  ws_subscribe: [UP, DOWN]
12:34:52  ws_frame_received: snapshot        ← Initial snapshot
12:34:52  persist_decision: first_snapshot   ← Makes sense
12:34:52  persist_book                       ← INSERT #1 (legitimate)

... 40 seconds of quiet market ...

12:35:30  ws_stale_timeout                   ← No activity
12:35:30  DISCONNECTED
12:35:31  RECONNECTED                        ← NEW CONNECTION
12:35:31  ws_subscribe: [UP, DOWN]           ← Re-subscribe
12:35:32  ws_frame_received: snapshot        ← Server replays
12:35:32  persist_decision: first_snapshot   ← BUG! New BookState thinks first
12:35:32  persist_book                       ← INSERT #2 (DUPLICATE!)
```

Key indicators:
- Same `ts` (12:34:52) appears at 12:35:32 (server sent stale snapshot)
- `reason=first_snapshot` appears TWICE
- RECONNECTED event between the two persist_book calls

---

## Files to Read

1. **`DUPLICATE_INVESTIGATION.md`** — Full investigation guide
2. **`INSTRUMENTATION_SUMMARY.md`** — What was changed
3. **`PROGRESS_TRACKER.md`** — TaskRC status

---

## After Evidence Collection

Once you have proof of root cause from logs:

1. Document findings in `DUPLICATE_EVIDENCE_REPORT.md`
2. Share with team/reviewer
3. Design fix (likely: tie BookState to round, not connection)
4. Implement fix with tests
5. Verify fix eliminates duplicates

---

## Questions?

The full investigation methodology is in `DUPLICATE_INVESTIGATION.md`.
