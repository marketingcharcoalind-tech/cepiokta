# Logging Verbosity Reduction - Report

## Problem

Soak-run readonly (Fase Pre-G1) menghasilkan log 22GB, membuat disk VPS penuh dan bot restart ribuan kali. Logging instrumentation TaskRC (frame WebSocket + persist decisions) terlalu verbose untuk soak 24h+.

## Solution: Pure Logging Reduction

**TIDAK mengubah behavior trading/persistence/strategy** - hanya mengurangi verbosity log.

### Changes Implemented

#### 1. WebSocket Frame Logging → DEBUG Level
**File**: `src/btcbot/adapters/clob_ws.py`

```python
# BEFORE: log.info("ws_frame_received", ...)
# AFTER:  log.debug("ws_frame_received", ...)

# BEFORE: log.info("ws_parser_output", ...)
# AFTER:  log.debug("ws_parser_output", ...)
```

Frame-level events (`ws_frame_received`, `ws_parser_output`) sekarang hanya muncul saat `LOG_LEVEL=DEBUG`. Default `LOG_LEVEL=INFO` tidak mencatat frame per frame.

#### 2. Persist Decision Logging → Gated Behind Flag
**File**: `src/btcbot/data/recorder.py`

Semua 6 log `persist_decision` sekarang di-gate oleh `instrumentation_verbose` flag:

```python
if self._instrumentation_verbose:
    log.info("persist_decision", ...)
```

**Reasons affected:**
- `persist_mode_all`
- `first_snapshot`
- `price_changed`
- `finegrain_mode`
- `throttle_expired`
- `throttle_active`

#### 3. New Environment Flag: `INSTRUMENTATION_VERBOSE`
**Files**: `src/btcbot/config/settings.py`, `.env.example`

```python
# Settings
instrumentation_verbose: bool = False  # Default: off
```

```bash
# .env
INSTRUMENTATION_VERBOSE=false  # TaskRC debug: log semua frame/persist (huge logs)
```

**Default `false`** = hanya event penting (boot, round result, resolusi, error).
**Set `true`** = semua frame/persist untuk debugging duplicate investigation.

#### 4. Wiring
**File**: `src/btcbot/app/cli.py`

```python
recorder = Recorder(
    ...
    instrumentation_verbose=settings.instrumentation_verbose,
)
```

Flag dari environment → Settings → Recorder.

## Verification

### Tests: ✅ ALL PASS
```
======================= 1183 passed, 1 warning in 8.65s ==============
```

**No behavior changes:**
- Persistence logic unchanged
- Throttle unchanged
- Finegrain unchanged
- WebSocket parsing unchanged
- All test_recorder.py tests pass (retensi correct)
- All test_cli.py tests pass (integration correct)

### What's Logged at INFO (Default)

**With `INSTRUMENTATION_VERBOSE=false` (default):**
- ✅ Boot sequence
- ✅ Round discovery
- ✅ Round start/end (`recorder_book_received`, summary)
- ✅ `persist_book` (actual write events)
- ✅ Resolution
- ✅ Errors, circuit breaker, gaps
- ✅ Heartbeat
- ❌ NO `ws_frame_received`
- ❌ NO `ws_parser_output`
- ❌ NO `persist_decision`

**With `INSTRUMENTATION_VERBOSE=true` (debug):**
- Everything above +
- ✅ `persist_decision` (all 6 reasons)
- But still NO frame logs (need `LOG_LEVEL=DEBUG`)

**With `LOG_LEVEL=DEBUG`:**
- Everything +
- ✅ `ws_frame_received`
- ✅ `ws_parser_output`

## Impact Estimate

**Before (TaskRC instrumentation):**
- Frame logs: ~1000/min × 2 tokens = 2000 log lines/min
- Persist decisions: ~500/min × 2 tokens = 1000 log lines/min
- **Total instrumentation**: ~3000 lines/min
- 24h soak: 3000 × 60 × 24 = **4.3M log lines** → 22GB+

**After (default INSTRUMENTATION_VERBOSE=false, LOG_LEVEL=INFO):**
- Frame logs: 0
- Persist decisions: 0
- Only: `persist_book` (actual writes, ~450/ronde), heartbeat, round events
- **Total**: ~500 lines/min
- 24h soak: 500 × 60 × 24 = **720K log lines** → ~3-4GB

**Reduction: ~83% fewer log lines, ~82% smaller log files**

## Usage

### Normal Soak (24h+ production)
```bash
# .env
LOG_LEVEL=INFO
INSTRUMENTATION_VERBOSE=false  # Default
```

### Debug Duplicate Investigation
```bash
# .env
LOG_LEVEL=INFO
INSTRUMENTATION_VERBOSE=true  # See persist_decision
```

### Full Debug (frame-level)
```bash
# .env
LOG_LEVEL=DEBUG  # See ws_frame + ws_parser_output
INSTRUMENTATION_VERBOSE=true
```

## Next Steps

1. **Deploy to VPS** with default settings (`INSTRUMENTATION_VERBOSE=false`)
2. **Run 24h soak** and monitor disk usage
3. **Expected**: Log files ~3-4GB (vs 22GB before)
4. **If investigation needed**: Set `INSTRUMENTATION_VERBOSE=true` for specific debugging

## Files Changed

**Production:**
- `src/btcbot/config/settings.py` - add `instrumentation_verbose` flag
- `src/btcbot/adapters/clob_ws.py` - frame logs → DEBUG
- `src/btcbot/data/recorder.py` - gate persist_decision logs
- `src/btcbot/app/cli.py` - wire flag to Recorder
- `.env.example` - document flag

**Tests:**
- No test changes needed (behavior unchanged)

**Verification:**
- All 1183 tests PASS
- No logic changes
- Pure observability reduction
