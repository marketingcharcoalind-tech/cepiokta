# VPS Commands - book_snapshots PRIMARY KEY Fix

## STEP 1: Verification (Buktikan Kondisi)

**Jalankan di VPS:**

```bash
cd ~/cepiokta

echo "=== STEP 1a: Check Schema ==="
sqlite3 btcbot.db "SELECT sql FROM sqlite_master WHERE name='book_snapshots';"
echo ""

echo "=== STEP 1b: Count NULL IDs ==="
sqlite3 btcbot.db "SELECT COUNT(*) AS null_ids FROM book_snapshots WHERE id IS NULL;"
echo ""

echo "=== STEP 1c: Sample NULL IDs (10 rows) ==="
sqlite3 btcbot.db "SELECT round_no, token_id, ts, id FROM book_snapshots WHERE id IS NULL LIMIT 10;"
echo ""

echo "=== STEP 1d: ID Statistics ==="
sqlite3 btcbot.db "SELECT MIN(id) as min_id, MAX(id) as max_id, COUNT(DISTINCT id) as distinct_ids, COUNT(*) as total_rows FROM book_snapshots WHERE id IS NOT NULL;"
echo ""

echo "=== STEP 1e: Round Count ==="
sqlite3 btcbot.db "SELECT COUNT(DISTINCT round_no) as distinct_rounds FROM book_snapshots;"
```

**Paste output lengkap di sini.**

---

## STEP 2: Git Pull (Ambil Perubahan)

```bash
cd ~/cepiokta
git pull
```

---

## STEP 3: Run Fix Script

```bash
cd ~/cepiokta
bash scripts/fix_book_snapshots_pk.sh
```

**Script akan:**
- Buat backup: `btcbot.backup.YYYYMMDD_HHMMSS.db`
- Pre-check: hitung distinct round_no BEFORE
- Rebuild table dengan PK yang benar
- Post-check: verify round_no sama, id tidak NULL
- Tampilkan instruksi deployment

**Paste output lengkap script di sini.**

---

## STEP 4: Deploy Fixed DB (Hanya Jika Script Sukses)

**ONLY if script output shows "✅ VALIDATION PASSED":**

```bash
cd ~/cepiokta

# 1. Get the backup filename from script output
# It will be like: btcbot.backup.20260706_143022.db
BACKUP_FILE="btcbot.backup.YYYYMMDD_HHMMSS.db"  # Replace with actual

# 2. Stop bot
tmux attach -t soak
# Press Ctrl+C to stop bot
# Type: exit

# 3. Backup current (broken) DB
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mv btcbot.db btcbot.broken.$TIMESTAMP.db

# 4. Copy fixed backup to live DB
cp $BACKUP_FILE btcbot.db

# 5. Verify file copied
ls -lh btcbot.db

# 6. Restart bot
tmux new -s soak
uv run python -m btcbot.app.cli

# Detach from tmux: Ctrl+B then D
```

---

## STEP 5: Verify Fix Working

**Wait 5-10 minutes for bot to write new data, then check:**

```bash
cd ~/cepiokta

echo "=== New rows should have valid IDs ==="
sqlite3 btcbot.db "SELECT id, round_no, token_id, ts FROM book_snapshots ORDER BY id DESC LIMIT 10;"
echo ""

echo "=== No NULL IDs ==="
sqlite3 btcbot.db "SELECT COUNT(*) FROM book_snapshots WHERE id IS NULL;"
echo ""

echo "=== Schema (should have PRIMARY KEY AUTOINCREMENT) ==="
sqlite3 btcbot.db "SELECT sql FROM sqlite_master WHERE name='book_snapshots';" | head -5
```

**Expected:**
- All recent ids are NOT NULL
- NULL id count = 0
- Schema contains "INTEGER PRIMARY KEY AUTOINCREMENT"

---

## STEP 6: Run Regression Tests

```bash
cd ~/cepiokta
uv run pytest tests/data/test_book_snapshots_pk.py -q
```

**Paste ACTUAL test output di sini.**

**Expected output:**
```
......                                                            [100%]
6 passed in X.XXs
```

---

## Summary Checklist

- [ ] STEP 1: Verified schema & NULL id count (paste output above)
- [ ] STEP 2: Git pull successful
- [ ] STEP 3: Fix script ran, showed "✅ VALIDATION PASSED"
- [ ] STEP 4: Deployed fixed DB (stopped bot, replaced DB, restarted)
- [ ] STEP 5: Verified new rows have valid ids (wait 5-10 min first)
- [ ] STEP 6: All regression tests PASSED (paste output above)

---

## Rollback (If Something Goes Wrong)

```bash
cd ~/cepiokta

# Stop bot
tmux attach -t soak
# Ctrl+C, exit

# Restore original
TIMESTAMP=YYYYMMDD_HHMMSS  # From step 4
mv btcbot.db btcbot.failed_fix.$TIMESTAMP.db
mv btcbot.broken.$TIMESTAMP.db btcbot.db

# Restart bot
tmux new -s soak
uv run python -m btcbot.app.cli
# Ctrl+B then D
```
