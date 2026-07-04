#!/usr/bin/env python3
"""Verify discovery strategy: 12h buffer vs by-slug approach."""

import asyncio
import json
from datetime import datetime, UTC, timedelta
import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"

async def test_12h_buffer():
    """Test if 12h buffer gets capped at 500 markets."""
    now = datetime.now(UTC)
    end_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_max = (now + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    url = f"{GAMMA_BASE}/markets"
    params = {
        "closed": "false",
        # NOTE: 'active' might conflict with end_date filters - testing without it
        "end_date_min": end_min,
        "end_date_max": end_max,
        "limit": 500,
    }
    
    print(f"\n{'='*80}")
    print(f"TEST 1: 12h buffer approach")
    print(f"{'='*80}")
    print(f"Query: {url}")
    print(f"Params: {json.dumps(params, indent=2)}")
    
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            print(f"\nERROR: Status {resp.status_code}")
            print(f"Response body: {resp.text[:500]}")
            return True, []  # Assume capped/failed
        markets = resp.json()
    
    total = len(markets)
    btc_5m = [m for m in markets if "btc-updown-5m" in m.get("slug", "")]
    
    print(f"\nResults:")
    print(f"  Total markets: {total}")
    print(f"  BTC 5m markets: {len(btc_5m)}")
    print(f"  Hit 500 cap? {'YES [WARNING]' if total >= 500 else 'NO [OK]'}")
    
    if btc_5m:
        print(f"\n  First 3 BTC 5m markets:")
        for m in btc_5m[:3]:
            print(f"    - {m['slug']}: endDate={m.get('endDate', 'N/A')}")
    else:
        print(f"  [WARNING] NO BTC 5m markets found in 12h buffer!")
    
    return total >= 500, btc_5m

async def test_narrow_windows():
    """Test narrow windows to find when next BTC 5m market resolves."""
    now = datetime.now(UTC)
    
    print(f"\n{'='*80}")
    print(f"TEST 2: When do BTC 5m markets actually resolve?")
    print(f"{'='*80}")
    print(f"Current time: {now.isoformat()}")
    
    # Test windows: next 15min, 1h, 3h, 6h, 12h
    windows = [
        ("15 min", timedelta(minutes=15)),
        ("1 hour", timedelta(hours=1)),
        ("3 hours", timedelta(hours=3)),
        ("6 hours", timedelta(hours=6)),
        ("12 hours", timedelta(hours=12)),
    ]
    
    results = {}
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        for label, delta in windows:
            end_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_max = (now + delta).strftime("%Y-%m-%dT%H:%M:%SZ")
            
            params = {
                "closed": "false",
                # NOTE: 'active' might conflict with end_date filters
                "end_date_min": end_min,
                "end_date_max": end_max,
            }
            
            resp = await client.get(f"{GAMMA_BASE}/markets", params=params)
            resp.raise_for_status()
            markets = resp.json()
            
            btc_5m = [m for m in markets if "btc-updown-5m" in m.get("slug", "")]
            results[label] = btc_5m
            
            print(f"\n  Window {label} ahead: {len(btc_5m)} BTC 5m markets")
            if btc_5m:
                earliest = min(btc_5m, key=lambda m: m.get("endDate", ""))
                print(f"    Earliest: {earliest['slug']}, endDate={earliest.get('endDate')}")
    
    return results

async def test_by_slug():
    """Test by-slug approach: calculate active window epoch and fetch directly."""
    now = datetime.now(UTC)
    
    print(f"\n{'='*80}")
    print(f"TEST 3: By-slug approach (calculate active window)")
    print(f"{'='*80}")
    
    # Calculate current 5m window epoch (aligned to 300s, slug = window_start)
    now_ts = int(now.timestamp())
    window_start_epoch = (now_ts // 300) * 300
    
    # Try current window and next 3 windows
    epochs = [window_start_epoch + (i * 300) for i in range(4)]
    
    print(f"Current timestamp: {now_ts}")
    print(f"Testing epochs (aligned 5m windows):")
    
    found = []
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        for epoch in epochs:
            slug = f"btc-updown-5m-{epoch}"
            dt = datetime.fromtimestamp(epoch, tz=UTC)
            
            # Try by slug
            resp = await client.get(f"{GAMMA_BASE}/markets", params={"slug": slug})
            resp.raise_for_status()
            markets = resp.json()
            
            status = "[OK] FOUND" if markets else "[X] not found"
            print(f"  {slug} ({dt.isoformat()}) -> {status}")
            
            if markets:
                m = markets[0]
                found.append(m)
                print(f"    closed={m.get('closed')}, active={m.get('active')}, endDate={m.get('endDate')}")
    
    return found

async def main():
    print(f"\n{'='*80}")
    print(f"DISCOVERY STRATEGY VERIFICATION")
    print(f"{'='*80}")
    
    # Test 1: Check if 12h buffer hits cap
    capped, btc_5m_in_buffer = await test_12h_buffer()
    
    # Test 2: Find when markets actually exist
    windows_results = await test_narrow_windows()
    
    # Test 3: By-slug approach
    by_slug_results = await test_by_slug()
    
    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY & RECOMMENDATION")
    print(f"{'='*80}")
    
    if capped:
        print(f"[WARNING] 12h buffer HITS 500-market cap -> BTC 5m may be dropped")
        print(f"    Recommendation: Use by-slug approach instead")
    else:
        print(f"[OK] 12h buffer does NOT hit cap -> safe to use")
    
    if not btc_5m_in_buffer and not by_slug_results:
        print(f"\n[WARNING] NO active BTC 5m markets found by ANY method")
        print(f"    Possible reasons:")
        print(f"    1. Markets are listed sparsely/in clusters (not continuous)")
        print(f"    2. No markets currently active (trading paused?)")
        print(f"    3. Polymarket scheduling changed")
    elif by_slug_results:
        print(f"\n[OK] By-slug approach found {len(by_slug_results)} markets")
        print(f"    Recommendation: Use by-slug for reliability")
    
    print(f"\n{'='*80}\n")

if __name__ == "__main__":
    asyncio.run(main())
