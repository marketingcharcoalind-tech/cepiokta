# Instrumentation Report — Add round_no to _should_persist()

**Date**: 2026-07-02  
**Purpose**: Add `round_no` field to all `persist_decision` logs for debugging observability

---

## Objective

Enable filtering `persist_decision` logs by `round_no` to distinguish between:
- Multiple rounds being analyzed together
- Single round behavior
- Whether `_should_persist()` is actually being called for all `recorder_book_received` events

**Problem observed**:
- `recorder_book_received`: ~94,844 events in one analysis
- `persist_book`: 454 events
- `persist_decision`: unable to filter by round_no (field was missing)

---

## Changes Made

### 1. Updated `_should_persist()` Signature

**Location**: `src/btcbot/data/recorder.py`

**Old signature**:
```python
def _should_persist(
    self,
    book: OrderBook,
    window_end: datetime | None,
    now: datetime,
) -> bool:
```

**New signature**:
```python
def _should_persist(
    self,
    round_no: int,
    book: OrderBook,
    window_end: datetime | None,
    now: datetime,
) -> bool:
```

**Change**: Added `round_no: int` as the FIRST parameter (after `self`).

---

### 2. Updated Call Sites

#### Production Code — `src/btcbot/data/recorder.py`

**Location**: Line ~253 in `consume_market()`

**Old**:
```python
if self._should_persist(book, window_end, now):
    await self._persist_book(round_no, book, now)
```

**New**:
```python
if self._should_persist(round_no, book, window_end, now):
    await self._persist_book(round_no, book, now)
```

**Total**: 1 call site in production code

---

#### Test Code — `tests/data/test_recorder.py`

**Class**: `TestShouldPersist`

All 6 test methods updated:

1. **`test_first_always_persists`** (Line ~267)
   - Old: `rec._should_persist(_book(), None, clock.now())`
   - New: `rec._should_persist(1, _book(), None, clock.now())`

2. **`test_same_best_within_sample_throttled`** (Line ~274)
   - Old: `rec._should_persist(_book(bid_size="101"), None, WS)`
   - New: `rec._should_persist(1, _book(bid_size="101"), None, WS)`

3. **`test_same_best_after_sample_persists`** (Line ~281)
   - Old: `rec._should_persist(_book(bid_size="101"), None, later)`
   - New: `rec._should_persist(1, _book(bid_size="101"), None, later)`

4. **`test_best_change_persists_immediately`** (Line ~288)
   - Old: `rec._should_persist(_book(bid="0.53"), None, WS)`
   - New: `rec._should_persist(1, _book(bid="0.53"), None, WS)`

5. **`test_finegrain_window_bypasses_throttle`** (Line ~295)
   - Old: `rec._should_persist(_book(bid_size="101"), window_end, WS)`
   - New: `rec._should_persist(1, _book(bid_size="101"), window_end, WS)`

6. **`test_mode_all_always_persists`** (Line ~301)
   - Old: `rec._should_persist(_book(bid_size="101"), None, WS)`
   - New: `rec._should_persist(1, _book(bid_size="101"), None, WS)`

**Total**: 6 test call sites (all updated with `round_no=1` as dummy value)

---

### 3. Updated All `persist_decision` Logs

**Location**: `src/btcbot/data/recorder.py` in `_should_persist()`

All 6 logging locations now include `round_no=round_no`:

#### Reason #1: `persist_mode_all`
```python
log.info(
    "persist_decision",
    round_no=round_no,  # ← ADDED
    token_id=book.token_id,
    ts=book.ts.isoformat(),
    decision=True,
    reason="persist_mode_all",
)
```

#### Reason #2: `first_snapshot`
```python
log.info(
    "persist_decision",
    round_no=round_no,  # ← ADDED
    token_id=book.token_id,
    ts=book.ts.isoformat(),
    decision=True,
    reason="first_snapshot",
)
```

#### Reason #3: `price_changed`
```python
log.info(
    "persist_decision",
    round_no=round_no,  # ← ADDED
    token_id=book.token_id,
    ts=book.ts.isoformat(),
    decision=True,
    reason="price_changed",
    last_bid=str(last_bid) if last_bid is not None else None,
    last_ask=str(last_ask) if last_ask is not None else None,
    best_bid=str(best_bid) if best_bid is not None else None,
    best_ask=str(best_ask) if best_ask is not None else None,
)
```

#### Reason #4: `finegrain_mode`
```python
log.info(
    "persist_decision",
    round_no=round_no,  # ← ADDED
    token_id=book.token_id,
    ts=book.ts.isoformat(),
    decision=True,
    reason="finegrain_mode",
    seconds_left=(window_end - now).total_seconds(),
)
```

#### Reason #5 & #6: `throttle_expired` / `throttle_active`
```python
log.info(
    "persist_decision",
    round_no=round_no,  # ← ADDED
    token_id=book.token_id,
    ts=book.ts.isoformat(),
    decision=throttle_elapsed,
    reason="throttle_expired" if throttle_elapsed else "throttle_active",
    elapsed_ms=now_ms - last_ms,
    sample_ms=self._sample_ms,
)
```

**Total**: 6 log statements (all 6 reasons covered)

---

## Files Changed

| File | Type | Changes |
|------|------|---------|
| `src/btcbot/data/recorder.py` | Production | Signature + 1 call site + 6 log statements |
| `tests/data/test_recorder.py` | Test | 6 test call sites |

**Total files**: 2

---

## Verification

### ✅ Syntax Check
```bash
python -m py_compile src\btcbot\data\recorder.py tests\data\test_recorder.py
# Exit Code: 0 (success)
```

### ✅ All Call Sites Updated
```bash
grep -r "_should_persist(" --include="*.py"
```

Results:
- ✅ 1 production call site: `recorder.py:253` with `round_no`
- ✅ 6 test call sites: `test_recorder.py` (lines 267, 274, 281, 288, 295, 301) all with `round_no=1`
- ✅ 1 definition: `recorder.py:305` with new signature

### ✅ All Logs Have `round_no`
```bash
grep -A 5 "persist_decision" src/btcbot/data/recorder.py
```

Results:
- ✅ 6 log statements found
- ✅ All 6 have `round_no=round_no` field
- ✅ All 6 reasons covered:
  - `persist_mode_all`
  - `first_snapshot`
  - `price_changed`
  - `finegrain_mode`
  - `throttle_expired`
  - `throttle_active`

---

## Behavior Confirmation

**NO BEHAVIOR CHANGES**:

### ✅ Logic Unchanged
- Throttle logic: UNCHANGED (still uses `self._sample_ms`)
- Finegrain logic: UNCHANGED (still uses `self._finegrain_sec`)
- Persist mode logic: UNCHANGED (still checks `self._persist_mode`)
- Return values: UNCHANGED (same True/False conditions)
- State updates: UNCHANGED (`self._last_persist` logic identical)

### ✅ Flow Unchanged
- Recorder: UNCHANGED (still calls `_should_persist` same way)
- Database: UNCHANGED (no schema changes)
- WebSocket: UNCHANGED (no connection changes)
- Timing: UNCHANGED (no new sleeps/waits)

### ✅ Only Change
- **Logging**: Added `round_no` field to 6 `persist_decision` log statements
- **Signature**: Added `round_no` parameter for observability
- **Call sites**: Updated to pass `round_no` through

---

## Usage — Debugging with round_no

### Filter logs by specific round

```bash
# Extract all persist_decision for round 999
grep '"persist_decision"' investigation.log | grep '"round_no":999'
```

### Count decisions per round

```python
import json
from collections import Counter

decision_counts = Counter()
with open("investigation.log") as f:
    for line in f:
        try:
            log = json.loads(line)
            if log.get("event") == "persist_decision":
                round_no = log.get("round_no")
                decision_counts[round_no] += 1
        except:
            pass

for round_no, count in sorted(decision_counts.items()):
    print(f"Round {round_no}: {count} decisions")
```

### Verify _should_persist is called for every book

Compare counts per round:
- `recorder_book_received` count (should match books received)
- `persist_decision` count (should ALSO match if all are evaluated)
- `persist_book` count (subset where decision=True)

**Expected**:
```
Round 999:
  recorder_book_received: 10,000
  persist_decision: 10,000  ← Must match!
  persist_book: 500  ← Subset
```

**If mismatch**:
- `persist_decision < recorder_book_received` → Bug! Some books skip evaluation
- `persist_decision == recorder_book_received` → Correct behavior

---

## Summary

**Pure instrumentation change** — added `round_no` parameter and field for debugging observability.

**Changes**:
- ✅ Signature: `_should_persist(round_no, book, window_end, now)`
- ✅ Call sites: 1 production + 6 test (all updated)
- ✅ Logs: 6 `persist_decision` statements (all have `round_no` field)

**Behavior**:
- ✅ NO logic changes
- ✅ NO persistence changes
- ✅ NO flow changes
- ✅ NO timing changes

**Purpose**:
- Enable per-round filtering of `persist_decision` logs
- Verify `_should_persist()` is called for ALL received books
- Diagnose discrepancy: 94,844 books received vs 454 persisted

**Next step**: Run recorder and verify `persist_decision` count matches `recorder_book_received` count for the same `round_no`.
