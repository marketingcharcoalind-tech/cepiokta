# DISCOVERY BLOCKER FIX — Slug Epoch Convention & Discovery Strategy

**Date**: 2026-07-04  
**Status**: CRITICAL BLOCKER (bot cannot discover any markets)

---

## PROBLEM SUMMARY

Bot dry-run fails: `discover_active_round()` returns "tidak ada ronde btc-updown-5m ditemukan dari Gamma".

### Root Causes

1. **WRONG SLUG CONVENTION**: Current code assumes `slug epoch = window_END`, but reality is `slug epoch = window_START`
   - Example from VPS curl: `btc-updown-5m-1783200000`
   - `epoch 1783200000` = `2026-07-04 21:20:00Z` (window START)
   - `endDate` = `2026-07-04 21:25:00Z` (window END = epoch + 300s)
   - **Current code**: `_window_end()` returns `epoch` when endDate missing → WRONG
   - **Current code**: `_window_start()` returns `epoch - tf` when eventStartTime missing → WRONG

2. **DISCOVERY BUFFER TOO NARROW**: `DISCOVERY_BUFFER_SECONDS["5m"] = 12 * 60` (12 minutes)
   - Markets listed **~24 hours ahead** (startDate ≈ now, endDate = window 8h+ in future)
   - Current query: `end_date_min=now, end_date_max=now+12min` → misses everything
   - Active window at 13:01Z, nearest market resolves 21:25Z (~8h ahead) → buffer never sees it

---

## EVIDENCE (from VPS curl)

### Sample 1: btc-updown-5m-1783200000
```
slug: btc-updown-5m-1783200000
epoch: 1783200000 = 2026-07-04 21:20:00Z  ← WINDOW START
endDate: 2026-07-04T21:25:00Z             ← WINDOW END (epoch + 300s)
startDate: 2026-07-03T21:28:..Z           ← LISTING TIME (~24h earlier, IGNORE)
```

### Sample 2: btc-updown-5m-1783199700
```
slug: btc-updown-5m-1783199700
epoch: 1783199700 = 2026-07-04 21:15:00Z  ← WINDOW START
endDate: 2026-07-04T21:20:00Z             ← WINDOW END (epoch + 300s)
```

### Sample 3: btc-updown-5m-1783200300
```
slug: btc-updown-5m-1783200300
epoch: 1783200300 = 2026-07-04 21:25:00Z  ← WINDOW START
endDate: 2026-07-04T21:30:00Z             ← WINDOW END (epoch + 300s)
```

**PATTERN VERIFIED**: `slug_epoch = window_START`, `endDate = slug_epoch + timeframe_seconds`

### Discovery Window Test
```bash
# Query 21:20-21:30Z window → returns 3 markets (WORKS)
curl '.../markets?closed=false&end_date_min=2026-07-04T21:20:00Z&end_date_max=2026-07-04T21:30:00Z'
→ returns: btc-updown-5m-{1783199700, 1783200000, 1783200300}

# Current bot query at 13:01Z: end_date_min=13:01Z, end_date_max=13:13Z (12min buffer)
→ returns 0 markets (nearest endDate is 21:25Z, 8h+ in future)
```

---

## IMPACT ON EXISTING CODE

### 1. `market.py` Documentation — WRONG
Current docstring claims:
> ``epoch`` = waktu resolusi = ``window_end``; ``round_no = epoch``.

**Reality**: `epoch = window_START`, `round_no` should be `window_END`

Current `round_no_for()`:
```python
def round_no_for(now: datetime, timeframe: str) -> int:
    """Kembalikan ``round_no`` (= epoch ``window_end``) window yang memuat ``now``."""
    _, end = aligned_window(now, timeframe)
    return int(end.timestamp())  # ← Returns window_END, correct!
```
This is **CORRECT** (returns window_END), but contradicts the docstring and slug convention.

### 2. `gamma.py` Parser — WRONG
Current `_window_end()` and `_window_start()`:
```python
def _window_end(data: dict[str, Any], epoch: int) -> datetime:
    """``window_end`` = ``endDate`` bila ada, jika tidak dari slug epoch."""
    end_raw = data.get("endDate")
    if isinstance(end_raw, str) and end_raw:
        return _parse_utc(end_raw, "endDate")
    return datetime.fromtimestamp(epoch, tz=UTC)  # ← WRONG: epoch is START, not END

def _window_start(data: dict[str, Any], epoch: int, tf_seconds: int) -> datetime:
    """``window_start`` = ``eventStartTime``/``events[0].startTime`` else epoch-tf."""
    # ... eventStartTime logic ...
    return datetime.fromtimestamp(epoch - tf_seconds, tz=UTC)  # ← WRONG: should be epoch
```

### 3. `cli.py` round_no Calculation (FIX 2b) — CORRECT
```python
round_no = int(meta.end_time.timestamp())  # window_end epoch
```
This is **CORRECT** — round_no should be window_END for DB key consistency.

### 4. Discovery Strategy — BROKEN
Buffer too narrow to see markets listed far ahead.

---

## FIX STRATEGY

### FIX D1: Correct Parser (gamma.py)
```python
def _window_start(data: dict[str, Any], epoch: int, tf_seconds: int) -> datetime:
    """``window_start`` = ``eventStartTime``/``events[0].startTime`` else slug epoch.
    
    VERIFIED: slug epoch = window_START (endDate = epoch + tf).
    """
    ev_start = data.get("eventStartTime")
    if not isinstance(ev_start, str) or not ev_start:
        events = data.get("events")
        if isinstance(events, list) and events and isinstance(events[0], dict):
            candidate = events[0].get("startTime")
            ev_start = candidate if isinstance(candidate, str) else None
    if isinstance(ev_start, str) and ev_start:
        return _parse_utc(ev_start, "eventStartTime")
    # CORRECTED: slug epoch = window_START
    return datetime.fromtimestamp(epoch, tz=UTC)

def _window_end(data: dict[str, Any], epoch: int, tf_seconds: int) -> datetime:
    """``window_end`` = ``endDate`` bila ada, jika tidak slug epoch + timeframe.
    
    VERIFIED: slug epoch = window_START, endDate = epoch + tf.
    """
    end_raw = data.get("endDate")
    if isinstance(end_raw, str) and end_raw:
        return _parse_utc(end_raw, "endDate")
    # CORRECTED: slug epoch + timeframe = window_END
    return datetime.fromtimestamp(epoch + tf_seconds, tz=UTC)
```

**Change**: 
- `_window_start` fallback: `epoch - tf_seconds` → `epoch`
- `_window_end` fallback: `epoch` → `epoch + tf_seconds`
- Add `tf_seconds` parameter to `_window_end()`
- Update `parse_market()` call: `end_time=_window_end(data, epoch, tf_seconds)`

### FIX D2: Widen Discovery Buffer (gamma.py)
Current markets listed ~24h ahead, active 8h+ in future. Widen buffer significantly:

```python
# OLD (too narrow):
DISCOVERY_BUFFER_SECONDS: dict[str, int] = {"5m": 12 * 60, "15m": 30 * 60}

# NEW (wide enough to catch markets listed ahead):
DISCOVERY_BUFFER_SECONDS: dict[str, int] = {
    "5m": 12 * 60 * 60,   # 12 hours (covers 8h ahead + margin)
    "15m": 12 * 60 * 60,  # 12 hours
}
```

**Rationale**: Query `end_date_min=now, end_date_max=now+12h` will catch markets with endDate up to 12h in future, covering current listing pattern.

### FIX D3: Update Documentation (market.py)
Correct docstring to match reality:

```python
"""domain/market.py — interval-loader (docs/08 §8.6, docs/05).

...

Fakta cadence terverifikasi (lihat PROMPT_GUIDE ✅ VERIFIED REALITY #1):

- Slug market: ``{asset}-updown-{5m|15m}-{epoch}`` (mis. ``btc-updown-5m-1783200000``).
- ``epoch`` = **window_START** (waktu mulai window); ``endDate`` = ``epoch + timeframe``.
- ``round_no`` = window_END epoch (untuk konsistensi DB key & market.round_no_for).
- Cadence: ``epoch % 300 == 0`` (5m) / ``epoch % 900 == 0`` (15m).
- Cross-check: ``1783200000`` → ``2026-07-04T21:20:00Z`` (``window_start``);
  ``window_end`` = ``21:25:00Z``.

...
```

### FIX D4: Update PROGRESS_TRACKER Decision Log
Document the correction and its implications (BREAKING understanding, not data).

---

## TESTING PLAN

1. **Unit test**: Update `tests/adapters/test_gamma.py` to verify parser with correct convention
   - Mock market with slug `btc-updown-5m-1783200000`, `endDate: 2026-07-04T21:25:00Z`
   - Assert `meta.start_time = 21:20:00Z`, `meta.end_time = 21:25:00Z`
   - Test fallback (no endDate/eventStartTime): epoch → start, epoch+300 → end

2. **Integration test**: `python -m btcbot.app.cli --max-rounds 2` must:
   - Discover ≥1 active round
   - Record round with `price_samples > 0`
   - No crash, no "tidak ada ronde ditemukan"

3. **Verification**: Check DB after run:
   ```sql
   SELECT round_no, COUNT(*) FROM book_snapshots GROUP BY round_no;
   SELECT round_no, COUNT(*) FROM price_ticks GROUP BY round_no;
   ```
   Must have data for discovered rounds.

---

## SIDE EFFECTS

### BREAKING CHANGES
- **None** (round_no already uses window_END per FIX 2b, which is correct)
- Parser now computes correct window boundaries from slug epoch
- Discovery now sees markets listed far ahead

### NON-BREAKING
- FIX 2b (round_no = window_END) remains correct and unchanged
- DB schema unchanged
- Existing data keys unchanged (round_no was already window_END)

---

## COMMIT PLAN

1. **Commit D1**: Fix parser (`_window_start`, `_window_end` in gamma.py)
2. **Commit D2**: Widen buffer (`DISCOVERY_BUFFER_SECONDS` in gamma.py)
3. **Commit D3**: Update docs (market.py docstring)
4. **Commit D4**: Update PROGRESS_TRACKER decision log
5. **Verification run**: `cli --max-rounds 2`, confirm discovery works

All commits to branch `fix/discovery-blocker`, merge to main after verification.

---

## SUCCESS CRITERIA

- ✅ `python -m btcbot.app.cli --max-rounds 2` discovers ≥1 round
- ✅ Recorded round has `price_samples > 0`
- ✅ Parser test passes with corrected convention
- ✅ No crash, no "tidak ada ronde ditemukan"
- ✅ Documentation reflects reality (slug epoch = window_START)
