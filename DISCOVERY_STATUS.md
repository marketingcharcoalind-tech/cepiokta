# Discovery Fix - Current Status

## What Was Done

**commit `eeba4b9` (REVERTED conceptually, not yet pushed revert):**
- Changed `DISCOVERY_BUFFER_SECONDS` from 12 minutes to 12 hours
- Fixed slug epoch convention (epoch = window_START, not END)
- **PROBLEM**: 12h buffer with `end_date_min/max` is UNVERIFIED and risky (may hit 500-cap)

**New approach (IN PROGRESS, not yet pushed):**
- Implemented by-slug discovery: query individual epochs from aligned `now`
- Advantages: No 500-cap risk, proven reliable by user
- **PROBLEM**: 7 tests failing because tests mock bulk `/markets` endpoint, not by-slug queries

## Current Blocker

**Cannot verify dry-run works** because:
1. By-slug approach requires markets to exist at calculated epochs
2. User confirmed markets are sparse (~8h gap currently)
3. Tests fail because mock data doesn't match by-slug query pattern
4. Cannot run `python -m btcbot.app.cli --max-rounds 2` to verify LIVE behavior without fixing tests first

## User's Requirements (NOT MET)

From user's message:
1. ✅ D1: Slug epoch = window_START (DONE, verified)
2. ❌ D2: 12h buffer must be verified LIVE with curl (NOT DONE - actually risky)
3. ✅ D3/D4: Documentation fixes (DONE)
4. ❌ **Answer Question #2**: Why no markets in next 6 hours? (PARTIALLY - identified sparse/clustered, but not root cause)
5. ❌ **GATE**: `python -m btcbot.app.cli --max-rounds 2` must find rounds (CANNOT TEST - tests failing)

## Recommended Next Steps

### Option A: Fix Tests for By-Slug (THOROUGH but time-consuming)
1. Update all 7 test fixtures in `tests/adapters/test_gamma.py`
2. Mock individual `/markets?slug=btc-updown-5m-{epoch}` responses
3. Ensure test clock aligns with available market epochs
4. Run full test suite
5. Then verify dry-run LIVE

### Option B: Hybrid Approach (PRAGMATIC)
1. Keep by-slug as primary, but add fallback to old end_date approach for testing
2. Add environment variable `GAMMA_DISCOVERY_MODE=by_slug|end_date`
3. Tests use `end_date` mode (minimal changes)
4. Production uses `by_slug` mode (safer)
5. Verify dry-run LIVE

### Option C: Defer to User (HONEST)
1. Document current situation clearly
2. Ask user which approach they prefer
3. Note: Cannot verify LIVE from Windows environment (SSL/network issues)
4. User should test on VPS where they've already verified API behavior

## My Recommendation

**Option C** - The user has:
- VPS access where Gamma API works
- Already verified slug queries work
- Confirmed markets are sparse
- Can test dry-run immediately

I should:
1. Commit current by-slug implementation with failing tests
2. Document EXACTLY what tests need (mock by-slug queries)
3. Provide user with clear instructions to test on VPS
4. Let user decide: fix tests themselves, or revert to simpler approach

## Files Changed (uncommitted)

- `src/btcbot/adapters/gamma.py` - by-slug discovery implementation
- `DISCOVERY_FIX_V2.md` - detailed investigation findings
- `verify_discovery.py` - verification script (incomplete due to SSL issues)

## Tests Failing

1. `test_window_fallback_when_no_event_start` - expects old epoch calculation
2. `test_discover_rounds_btc5m_only` - no markets found (expects bulk query)
3. `test_discover_rounds_btc15m` - no markets found (expects bulk query)
4. `test_query_uses_end_date_window` - expects `end_date_min/max` params
5. `test_active_round_in_window` - no rounds found
6. `test_active_round_next_when_before_all` - no rounds found
7. `test_rate_limit_backoff_then_success` - unexpected retry count (145 instead of 2 - likely querying 144 epochs!)

**Test #7 reveals performance issue**: By-slug with 12h buffer = 144 individual queries for 5m markets! This is WORSE than 500-cap risk.

## CRITICAL REALIZATION

The by-slug approach with 12h buffer = **144 queries** for 5m (12h / 5min = 144 windows).

This is:
- Slower than bulk query
- More API calls (rate limit risk)
- Only makes sense if buffer is SMALL (e.g., next 4-6 windows = 20-30 minutes)

**BETTER HYBRID:**
- Use by-slug for NEXT FEW windows (e.g., 6 windows = 30 min for 5m)
- If markets are sparse, bot will naturally wait and retry discovery
- Reduces queries from 144 to 6
- Still avoids 500-cap
- Handles sparse markets gracefully

## Revised Recommendation

Implement **small-buffer by-slug** (6 windows ahead, ~30 min):
- 6 queries for 5m, 4 queries for 15m
- No 500-cap risk
- Reasonable API usage
- Handles sparse markets (bot retries discovery on next tick)
- Tests only need 6 mock responses instead of 144

This matches the ORIGINAL 12-minute buffer philosophy but uses safer by-slug method!
