# VPS Diagnostics - Book Snapshots Issue

**CRITICAL FINDING**: Code review shows NO BUG in recorder logic. All return statements are correctly placed. The `instrumentation_verbose=False` flag ONLY affects logging, NOT persistence logic.

## Action Required: Run These Commands on VPS

### 1. Check Production Logs Around Jul 4 23:32

```bash
# Look for WebSocket disconnect/reconnect events
grep "2026-07-04T23:3" /path/to/bot.log | grep -E "(disconnect|reconnect|circuit_event)"

# Check heartbeat logs to see if books are being consumed
grep "heartbeat" /path/to/bot.log | grep "2026-07-04T23:3"

# Look for recorder_book_received logs (added in logging reduction commit)
grep "recorder_book_received" /path/to/bot.log | tail -50
```

### 2. Run Regression Tests

```bash
cd /path/to/5min-btc-polymarket-blueprint-v1.3
uv run pytest tests/data/test_recorder.py::TestInstrumentationVerboseRegression -v
```

**Expected**: All 3 tests should PASS (proving code is correct)

### 3. Run Manual Diagnostic Script

```bash
uv run python test_instrumentation_manual.py
```

**Expected**: Should print "✅ SUCCESS: Book snapshot inserted!"

### 4. Check Database Manually

```bash
sqlite3 btcbot.db
```

```sql
-- Check recent rounds
SELECT round_no, window_end, status 
FROM rounds 
WHERE round_no > 1783208100 
ORDER BY round_no DESC 
LIMIT 10;

-- Check book_snapshots distribution
SELECT 
    DATE(ts) as date,
    COUNT(*) as snapshot_count,
    COUNT(DISTINCT round_no) as rounds_with_books
FROM book_snapshots 
WHERE gap = 0
GROUP BY DATE(ts)
ORDER BY date DESC;

-- Check if signals are still being written
SELECT 
    DATE(ts) as date,
    COUNT(*) as signal_count
FROM signals 
GROUP BY DATE(ts)
ORDER BY date DESC;

-- Try manual INSERT to verify table is writable
INSERT INTO book_snapshots (round_no, token_id, ts, gap, mode) 
VALUES (99999999, 'test_diag', datetime('now'), 0, 'readonly');

SELECT * FROM book_snapshots WHERE round_no = 99999999;

DELETE FROM book_snapshots WHERE round_no = 99999999;
```

### 5. Check Current Bot Status

```bash
# Is bot still running?
ps aux | grep python | grep btcbot

# Check latest log entries
tail -100 /path/to/bot.log

# Check for recent heartbeat (should show consumed > 0 if books are flowing)
grep "heartbeat" /path/to/bot.log | tail -5
```

## What To Look For

### If WebSocket is the problem:
- Heartbeat logs show `consumed=0` consistently
- No `recorder_book_received` logs after Jul 4 23:32
- `circuit_event` logs showing DISCONNECTED/GAVE_UP

**Fix**: Restart bot to re-establish WebSocket connection

### If tests PASS:
- Confirms code logic is correct
- Problem is NOT with `instrumentation_verbose` flag
- Focus on external factors (WebSocket, API, config)

### If tests FAIL:
- There IS a bug in the code (unexpected!)
- Share full test output for analysis

### If manual DB INSERT fails:
- Database permission issue
- Check file permissions: `ls -la btcbot.db`
- Check disk space: `df -h`

## Quick Recovery Steps

If you want to just get book_snapshots recording again:

```bash
# 1. Backup current DB
cp btcbot.db btcbot.db.backup.$(date +%Y%m%d_%H%M%S)

# 2. Restart bot (this will re-establish WebSocket)
pkill -f "python -m btcbot.app.cli"
nohup uv run python -m btcbot.app.cli > bot.log 2>&1 &

# 3. Wait 5 minutes, then check
tail -50 bot.log | grep recorder_book_received
```

## Report Back

Please run the above diagnostics and share:
1. Output of test suite
2. Output of manual diagnostic script  
3. Recent heartbeat logs (last 5 entries)
4. Any circuit_event or disconnect logs around Jul 4 23:32
5. Whether manual DB INSERT succeeded

This will tell us the actual root cause.
