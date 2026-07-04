# Discovery Fix V2 - By-Slug Approach

## Problem Summary

**Original commit `eeba4b9` had UNVERIFIED assumptions:**

1. ✅ **D1 CORRECT**: Slug epoch = window_START (verified with 3+ samples)
2. ❌ **D2 RISKY**: Expanding buffer 12min→12h UNVERIFIED
   - Risk: Wide end_date window may hit 500-market cap, dropping BTC 5m markets
   - From user's VPS tests: Gamma API returns `400 Bad Request` with `end_date_min/max` + `active` combination
   - Narrow window (21:20-21:30Z) worked BECAUSE few markets in range
3. ✅ **D3/D4 CORRECT**: Documentation fixes for slug epoch convention

## User's Verified Evidence

From user's curl tests on VPS:
- `/markets?slug=btc-updown-5m-<epoch>` → **RELIABLE**, returns exact market
- `/markets?end_date_min=X&end_date_max=Y` with wide window → **UNRELIABLE**
  - Wide window hits 500 cap
  - May conflict with `active=true` parameter (400 error observed)
- Market listing pattern: Markets listed ~24h ahead, endDate far in future
- **No BTC 5m markets resolve in next ~8 hours** (current VPS time ~13:00Z, earliest market 21:25Z)
  - This answers user's Question #2: Markets are **sparse/clustered**, NOT continuous every 5 minutes

## Recommended Solution: By-Slug Discovery

### Strategy

Instead of querying by `end_date` window:
1. Calculate current 5m window epoch from `now` (aligned to 300s)
2. Fetch directly by slug: `/markets?slug=btc-updown-5m-{epoch}`
3. If not found, try next window (epoch + 300)
4. Repeat for N windows ahead (e.g., 12h = 144 windows)

### Advantages

- **No 500-market cap risk**: Fetches ONE market per query
- **No parameter conflicts**: Simple slug query, no date filters
- **Proven reliable**: User verified slug queries work consistently
- **Handles sparse markets**: Will find next available market even if hours ahead

### Implementation Plan

1. Add `discover_round_by_epoch(epoch: int) -> RoundMeta | None` - fetch single market by epoch
2. Modify `discover_rounds()`:
   - Calculate current aligned epoch
   - Query by-slug for next N epochs (e.g., 144 = 12h for 5m)
   - Filter found markets
3. Keep `DISCOVERY_BUFFER_SECONDS` but use it to calculate **how many epochs to try**, not end_date window
4. Add tests with mock by-slug responses

### Code Changes Needed

**`src/btcbot/adapters/gamma.py`:**
```python
async def _get_market_by_slug(self, slug: str) -> dict[str, Any] | None:
    """Fetch single market by slug. Returns None if not found."""
    params = {"slug": slug}
    batch = await self._get_markets(params)
    if batch and isinstance(batch[0], dict):
        return batch[0]
    return None

async def discover_rounds(self) -> list[RoundMeta]:
    """Kumpulkan ronde up/down via by-slug (reliable, no cap risk)."""
    now = self._clock.now()
    buffer_sec = DISCOVERY_BUFFER_SECONDS[self._timeframe]
    tf_sec = TIMEFRAME_SECONDS[self._timeframe]
    
    # Calculate how many windows to check
    num_windows = buffer_sec // tf_sec
    
    # Start from current aligned epoch
    now_ts = int(now.timestamp())
    start_epoch = (now_ts // tf_sec) * tf_sec
    
    rounds: list[RoundMeta] = []
    for i in range(num_windows):
        epoch = start_epoch + (i * tf_sec)
        slug = f"{self._asset}-updown-{self._timeframe}-{epoch}"
        
        raw = await self._get_market_by_slug(slug)
        if raw and is_updown_market(raw, self._asset, self._timeframe):
            rounds.append(parse_market(raw))
    
    rounds.sort(key=lambda r: r.start_time)
    return rounds
```

## Gate Conditions (User's Requirements)

Before claiming "fixed":

1. ✅ Verify slug epoch = window_START with ≥3 samples (DONE by user)
2. ❌ **BLOCKED**: Cannot verify 12h buffer from Windows environment (400 error)
3. ✅ Implement by-slug approach (safer, user-verified)
4. ✅ Answer Question #2: Markets are sparse/clustered (verified: 8h gap)
5. ❌ **REQUIRED**: `python -m btcbot.app.cli --max-rounds 2` must find rounds with price_samples > 0

## Status

- **commit `eeba4b9` pushed prematurely** - contains UNVERIFIED 12h buffer approach
- **Next**: Implement by-slug approach, verify with dry-run, then push fix
