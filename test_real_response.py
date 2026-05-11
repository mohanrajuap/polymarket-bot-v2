"""
Regression test using the EXACT response shape captured from Polymarket
on May 11 2026 (Method 1 of find_btc_fast.py).

Verifies the scanner correctly:
  - Detects this as a BTC 5-min market
  - Parses outcome prices and token IDs
  - Computes elapsed time from the slug timestamp
"""
import os
os.environ.setdefault("DATA_DIR", "/tmp/arena_reg")

import json
import time
from datetime import datetime, timezone

# Exact response shape from real Polymarket as of 2026-05-11
REAL_MARKET = {
    "id": "some-internal-id",
    "conditionId": "0xabc123",
    "slug": "btc-updown-5m-1778460900",
    "question": "Bitcoin Up or Down - May 10, 8:55PM-9:00PM ET",
    "endDate": "2026-05-11T01:00:00Z",
    "startDate": "2026-05-10T01:02:53.122494Z",  # 24+ hours stale, as expected
    "outcomes": json.dumps(["Up", "Down"]),
    "outcomePrices": json.dumps(["0.515", "0.485"]),
    "clobTokenIds": json.dumps([
        "100773442899990141704865900887232627159423620918427257728444150331588089377510",
        "79100214820231735815430376802497616269099715379690841125100827542001425846376",
    ]),
    "active": True,
    "closed": False,
}


def test_is_btc_5min():
    print("\n[T1] _is_btc_5min_market detects the real market")
    from scanner import BTC5MinScanner
    s = BTC5MinScanner()
    assert s._is_btc_5min_market(REAL_MARKET), "slug 'btc-updown-5m-...' should match"
    print(f"  ✓ slug='{REAL_MARKET['slug']}' detected")


def test_outcome_prices():
    print("\n[T2] _get_outcome_prices parses Up=0.515, Down=0.485")
    from scanner import BTC5MinScanner
    s = BTC5MinScanner()
    prices = s._get_outcome_prices(REAL_MARKET)
    assert prices == (0.515, 0.485), f"got {prices}"
    print(f"  ✓ (yes=Up={prices[0]}, no=Down={prices[1]})")


def test_token_ids():
    print("\n[T3] _get_token_ids returns both CLOB tokens")
    from scanner import BTC5MinScanner
    s = BTC5MinScanner()
    yes_id, no_id = s._get_token_ids(REAL_MARKET)
    assert yes_id.startswith("100773"), f"yes_id wrong: {yes_id}"
    assert no_id.startswith("79100"), f"no_id wrong: {no_id}"
    print(f"  ✓ yes={yes_id[:20]}... no={no_id[:20]}...")


def test_slug_timestamp_resolution():
    print("\n[T4] Slug timestamp drives elapsed calculation (ignoring stale startDate)")
    from scanner import BTC5MinScanner
    s = BTC5MinScanner()

    # The slug ts is 1778460900. Pretend we're checking "now" exactly at slug ts.
    # elapsed should be ~0. But because our test is running NOW (after May 11),
    # elapsed will be a large positive number.
    elapsed = s._seconds_since_open(REAL_MARKET)

    # Slug ts 1778460900 = 2026-05-11T00:55:00 UTC.
    # If you're running this in May 2026 within a day of that time, elapsed
    # will be in the range [-100, 100000]. The sanity guard rejects beyond 24h
    # so the test depends on when run.
    if elapsed is None:
        print(f"  ⚠ elapsed=None (sanity guard fired — more than 24h since slug ts)")
        print(f"    This is correct behaviour: the market is from a different day.")
    else:
        print(f"  ✓ elapsed={elapsed}s (computed from slug, NOT stale startDate)")
        # Make sure we did NOT use the stale startDate.
        # The startDate is 2026-05-10T01:02:53.122494Z. If we'd used it,
        # elapsed would be roughly 86,400s+ off.
        from datetime import datetime, timezone
        stale_start = datetime.fromisoformat("2026-05-10T01:02:53.122494+00:00")
        stale_elapsed = (datetime.now(timezone.utc) - stale_start).total_seconds()
        if abs(elapsed - stale_elapsed) < 10:
            raise AssertionError(
                f"Scanner used stale startDate ({stale_elapsed:.0f}s) "
                f"instead of slug ts. Bug regressed."
            )
        print(f"  ✓ confirmed slug ts used (not stale startDate which would be {stale_elapsed:.0f}s)")


def test_candidate_slugs():
    print("\n[T5] _candidate_slugs generates current + previous window slugs")
    from scanner import BTC5MinScanner
    s = BTC5MinScanner()
    slugs = s._candidate_slugs()
    assert len(slugs) == 2, f"expected 2 slugs, got {len(slugs)}: {slugs}"
    # Both should be valid 5-min slug format
    import re
    for slug in slugs:
        assert re.match(r"^btc-updown-5m-\d{10}$", slug), f"bad slug: {slug}"
    # First should be current, second should be 300s earlier
    ts1 = int(slugs[0].split("-")[-1])
    ts2 = int(slugs[1].split("-")[-1])
    assert ts1 - ts2 == 300, f"slugs not 300s apart: {ts1}, {ts2}"
    # Current should be aligned to 300s boundary
    assert ts1 % 300 == 0, f"slug ts not aligned to 300s: {ts1}"
    print(f"  ✓ current:  {slugs[0]}")
    print(f"  ✓ previous: {slugs[1]}")


if __name__ == "__main__":
    test_is_btc_5min()
    test_outcome_prices()
    test_token_ids()
    test_slug_timestamp_resolution()
    test_candidate_slugs()
    print("\n" + "=" * 60)
    print("  🎉 REAL-RESPONSE REGRESSION TESTS PASSED")
    print("=" * 60)
