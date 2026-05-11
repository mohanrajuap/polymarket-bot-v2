"""
Polymarket API Diagnostic
─────────────────────────
Run this on YOUR machine. It will:
  1. Ping the Gamma API
  2. Try to fetch the live BTC 5-min market by slug (deterministic)
  3. Try the /markets list endpoint and show what BTC-related markets exist
  4. Print the raw shape of the first BTC market it finds

Usage:
    python diagnose_api.py
"""

import json
import time
from datetime import datetime, timezone

import requests

GAMMA = "https://gamma-api.polymarket.com"


def banner(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


def now_aligned_ts(seconds=300):
    now = int(time.time())
    open_ts = (now // seconds) * seconds
    return open_ts, open_ts + seconds


# ── 1. API reachable? ─────────────────────────────────────────
banner("STEP 1 — Can we even reach the Gamma API?")
try:
    r = requests.get(f"{GAMMA}/markets", params={"limit": 1}, timeout=10)
    print(f"  HTTP status: {r.status_code}")
    print(f"  Headers: server={r.headers.get('server')}, x-ratelimit={r.headers.get('x-ratelimit-remaining')}")
    if r.status_code != 200:
        print(f"  Response body: {r.text[:300]}")
        print("\n  ❌ Cannot reach Gamma API. Stop here, check network/proxy.")
        raise SystemExit(1)
    print("  ✓ Gamma API is reachable")
except Exception as e:
    print(f"  ❌ Network error: {e}")
    raise SystemExit(1)


# ── 2. Slug-based discovery ───────────────────────────────────
banner("STEP 2 — Try fetching the live BTC 5-min market by SLUG")
open_ts, close_ts = now_aligned_ts(300)
open_iso = datetime.fromtimestamp(open_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
close_iso = datetime.fromtimestamp(close_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
print(f"  Current window: open={open_iso}, close={close_iso}")
print(f"  Window UNIX: open={open_ts}, close={close_ts}")

slugs_to_try = [
    f"btc-updown-5m-{open_ts}",
    f"btc-updown-5m-{close_ts}",
    f"btc-updown-5m-{open_ts + 300}",   # next window
    f"btc-updown-5m-{close_ts + 300}",
    # alternate forms in case Polymarket changed scheme
    f"bitcoin-up-or-down-{open_ts}",
    f"btc-up-down-5m-{open_ts}",
]

found = []
for slug in slugs_to_try:
    try:
        r = requests.get(f"{GAMMA}/markets", params={"slug": slug, "limit": 1}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                found.append((slug, data[0]))
                print(f"  ✓ FOUND with slug: {slug}")
            elif isinstance(data, dict) and data.get("question"):
                found.append((slug, data))
                print(f"  ✓ FOUND with slug: {slug}")
            else:
                print(f"  ✗ {slug}: empty response")
        else:
            print(f"  ✗ {slug}: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ✗ {slug}: {e}")

if not found:
    print("\n  ⚠️ No market found by any candidate slug.")
    print("  This means Polymarket's slug scheme may have changed.")


# ── 3. List endpoint ──────────────────────────────────────────
banner("STEP 3 — List active markets, find anything BTC-related")
try:
    r = requests.get(
        f"{GAMMA}/markets",
        params={
            "active": "true",
            "closed": "false",
            "limit": 500,
            "order": "endDate",
            "ascending": "true",
        },
        timeout=15,
    )
    r.raise_for_status()
    all_markets = r.json()
    if not isinstance(all_markets, list):
        all_markets = []

    print(f"  Total active markets returned: {len(all_markets)}")

    # Bucket them
    btc_related = []
    for m in all_markets:
        q = (m.get("question") or "").lower()
        s = (m.get("slug") or "").lower()
        if "btc" in s or "btc" in q or "bitcoin" in q:
            btc_related.append(m)

    print(f"  BTC-related markets: {len(btc_related)}")

    if btc_related:
        print("\n  Top 5 BTC-related markets:")
        for m in btc_related[:5]:
            slug = (m.get("slug") or "")[:60]
            q = (m.get("question") or "")[:80]
            end = m.get("endDate") or m.get("end_date") or ""
            print(f"    slug:    {slug}")
            print(f"    question:{q}")
            print(f"    endDate: {end}")
            print(f"    ---")

    # Filter for 5-min specifically
    fivem = [m for m in btc_related
             if "5m" in (m.get("slug") or "").lower()
             or "5-min" in (m.get("question") or "").lower()]
    print(f"  BTC 5-min markets: {len(fivem)}")

except Exception as e:
    print(f"  ❌ list fetch error: {e}")


# ── 4. Inspect first match ────────────────────────────────────
target = None
if found:
    target = found[0][1]
elif 'fivem' in dir() and fivem:
    target = fivem[0]

if target:
    banner("STEP 4 — Raw shape of one BTC 5-min market")
    keys_to_show = [
        "id", "conditionId", "slug", "question", "active", "closed",
        "startDate", "endDate", "createdAt", "outcomePrices",
        "clobTokenIds", "outcomes",
    ]
    for k in keys_to_show:
        v = target.get(k)
        if v is None:
            continue
        if isinstance(v, str) and len(v) > 100:
            v = v[:100] + "..."
        print(f"  {k}: {v}")

    # Quick logic check: based on what the scanner thinks
    print("\n  Diagnostic check:")
    end = target.get("endDate")
    if end:
        try:
            end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            elapsed_from_end = (now - end_dt).total_seconds()
            print(f"    endDate is {-elapsed_from_end:+.0f}s from now "
                  f"(negative=still in future = market still open)")
        except Exception:
            pass
    start = target.get("startDate")
    if start:
        try:
            start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            elapsed_from_start = (now - start_dt).total_seconds()
            print(f"    startDate was {elapsed_from_start:+.0f}s ago "
                  f"(positive = market has been open this long, "
                  f"if startDate is reliable)")
        except Exception:
            pass

print("\n" + "=" * 70)
print("  DIAGNOSTIC COMPLETE")
print("=" * 70)
print("""
What this tells you:
  • If Step 1 fails → network/firewall issue.
  • If Step 2 finds nothing → Polymarket's slug scheme isn't what we expect.
    Look at the slugs in Step 3 to see the actual pattern in use.
  • If Step 3 shows 0 BTC 5-min markets → API isn't listing them (try a
    different filter or use the slug method only).
  • Step 4 shows you exactly what fields the bot has to work with.
""")
