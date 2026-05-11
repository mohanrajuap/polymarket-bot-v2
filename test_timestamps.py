"""
Regression test for the date/timestamp bug.

The bot was reporting "May 11, 3:40PM-3:45PM ET" markets when the user was
actually trading on May 10, because we trusted the unreliable `startDate`
field. This verifies the scanner now uses slug → endDate → startDate priority.
"""

import json
from datetime import datetime, timezone, timedelta

from scanner import BTC5MinScanner


def make_market(slug=None, start_date=None, end_date=None,
                question="Bitcoin Up or Down - test",
                outcome_prices=None, clob_token_ids=None):
    return {
        "id": "test-mkt-1",
        "conditionId": "test-mkt-1",
        "slug": slug,
        "question": question,
        "active": True,
        "closed": False,
        "startDate": start_date,
        "endDate": end_date,
        "outcomePrices": outcome_prices or json.dumps(["0.5", "0.5"]),
        "clobTokenIds": clob_token_ids or json.dumps(["yes-tok", "no-tok"]),
    }


def test_slug_takes_priority():
    print("\n[T1] slug-derived window-open beats wrong startDate")
    scanner = BTC5MinScanner()

    # Real market: opens NOW, closes in 5 min
    now = datetime.now(timezone.utc)
    close_ts = int((now + timedelta(seconds=300)).timestamp())

    # But Polymarket's `startDate` is wrong — claims it opened 24 hours ago
    bogus_start = (now - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    correct_end = (now + timedelta(seconds=300)).isoformat().replace("+00:00", "Z")

    m = make_market(
        slug=f"btc-updown-5m-{close_ts}",
        start_date=bogus_start,
        end_date=correct_end,
    )

    elapsed = scanner._seconds_since_open(m)
    print(f"  elapsed = {elapsed}s (expected ~0s, NOT ~86400s)")
    assert elapsed is not None
    assert -10 <= elapsed <= 10, f"slug should resolve to ~now, got {elapsed}"
    print("  ✅ PASS — bot correctly used slug, ignored bogus startDate")


def test_endDate_fallback():
    print("\n[T2] endDate - 300s fallback when slug missing")
    scanner = BTC5MinScanner()
    now = datetime.now(timezone.utc)
    end_iso = (now + timedelta(seconds=300)).isoformat().replace("+00:00", "Z")

    m = make_market(
        slug=None,                           # no slug
        start_date=None,                     # no start
        end_date=end_iso,                    # 5 min from now
    )
    elapsed = scanner._seconds_since_open(m)
    print(f"  elapsed = {elapsed}s (expected ~0s)")
    assert -10 <= elapsed <= 10
    print("  ✅ PASS")


def test_startDate_last_resort():
    print("\n[T3] startDate as last resort if slug + endDate missing")
    scanner = BTC5MinScanner()
    now = datetime.now(timezone.utc)
    start_iso = (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")

    m = make_market(
        slug=None,
        start_date=start_iso,                # 30s ago
        end_date=None,
    )
    elapsed = scanner._seconds_since_open(m)
    print(f"  elapsed = {elapsed}s (expected ~30s)")
    assert 25 <= elapsed <= 35
    print("  ✅ PASS")


def test_sanity_guard_rejects_bogus():
    print("\n[T4] sanity guard rejects markets with timestamps 24h+ off")
    scanner = BTC5MinScanner()
    far_past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    m = make_market(slug=None, start_date=far_past, end_date=None)
    elapsed = scanner._seconds_since_open(m)
    print(f"  elapsed = {elapsed} (expected None — too far in past)")
    assert elapsed is None
    print("  ✅ PASS")


def test_slug_detection():
    print("\n[T5] _is_btc_5min_market detects by slug pattern")
    scanner = BTC5MinScanner()

    # Slug-based positive
    m1 = make_market(slug="btc-updown-5m-1715361600",
                     question="anything")
    assert scanner._is_btc_5min_market(m1)
    print("  ✓ slug 'btc-updown-5m-...' detected")

    # Question-based positive (legacy fallback)
    m2 = make_market(slug=None,
                     question="Bitcoin Up or Down - May 9, 5:50AM-5:55AM ET")
    assert scanner._is_btc_5min_market(m2)
    print("  ✓ question text detected")

    # Negative: ETH market
    m3 = make_market(slug="eth-updown-5m-1715361600",
                     question="Ethereum Up or Down - 5:50-5:55AM ET")
    assert not scanner._is_btc_5min_market(m3)
    print("  ✓ ETH market rejected")

    # Negative: hourly BTC (only one time in question)
    m4 = make_market(slug="btc-updown-1h-1715361600",
                     question="Bitcoin Up or Down at 5pm ET")
    # Slug doesn't match `5m`, question only has one time → reject
    assert not scanner._is_btc_5min_market(m4)
    print("  ✓ hourly market rejected")

    print("  ✅ PASS")


if __name__ == "__main__":
    test_slug_takes_priority()
    test_endDate_fallback()
    test_startDate_last_resort()
    test_sanity_guard_rejects_bogus()
    test_slug_detection()
    print("\n" + "=" * 60)
    print("  🎉 ALL TIMESTAMP-RESOLUTION TESTS PASSED")
    print("=" * 60)
