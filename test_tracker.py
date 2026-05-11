"""
Test the market tracker and per-market summary flow.

Verifies:
  - Every observed market is tracked
  - Skip reasons are recorded (past deadline, no cheap side, global lock)
  - Closed markets generate a summary
  - Hourly digest aggregates correctly
"""

import os
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("DATA_DIR", "/tmp/arena_tracker")

import json
import shutil
import logging
import pathlib
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# Wipe and re-init
data_dir = pathlib.Path(os.environ["DATA_DIR"])
if data_dir.exists():
    shutil.rmtree(data_dir)

logging.basicConfig(level=logging.WARNING)

import db
db.init_db()

from market_tracker import market_tracker

# ─── Test 1: observe / skip / enter / exit lifecycle ─────────
def test_lifecycle():
    print("\n[TEST 1] market lifecycle in tracker")
    market_tracker._markets.clear()

    # Observe twice with different prices
    market_tracker.observe("m1", "Bitcoin Up or Down — 10:00–10:05 AM ET",
                           yes_price=0.30, no_price=0.70, elapsed_sec=5)
    market_tracker.observe("m1", "Bitcoin Up or Down — 10:00–10:05 AM ET",
                           yes_price=0.18, no_price=0.82, elapsed_sec=20)

    m = market_tracker.get("m1")
    assert m is not None
    assert m["min_yes_price"] == 0.18, f"min YES wrong: {m['min_yes_price']}"
    assert m["min_no_price"] == 0.70, f"min NO wrong: {m['min_no_price']}"
    assert m["tick_count"] == 2
    print(f"  ✓ observed twice, min_yes={m['min_yes_price']:.3f} min_no={m['min_no_price']:.3f}")

    # Mark skipped (e.g. global lock)
    market_tracker.mark_skipped("m1", market_tracker.REASON_GLOBAL_LOCK)
    m = market_tracker.get("m1")
    assert m["skip_reason"] == "another_position_open"
    print(f"  ✓ skip reason recorded: {m['skip_reason']}")

    # Now mark entered → skip_reason should clear
    market_tracker.mark_entered("m1", "yes", 0.18, 21)
    m = market_tracker.get("m1")
    assert m["entered"] is True
    assert m["skip_reason"] is None
    print(f"  ✓ entry recorded — skip reason cleared")

    # Mark exit
    market_tracker.mark_exited("m1", 0.30, 60, "TARGET_HIT", "win", 1.30)
    market_tracker.mark_closed("m1")
    m = market_tracker.get("m1")
    assert m["exit_outcome"] == "win"
    assert m["exit_pnl"] == 1.30
    assert m["closed"] is True
    print(f"  ✓ exit recorded: {m['exit_outcome']} ${m['exit_pnl']:+.2f}")
    print("  ✅ PASS")


# ─── Test 2: get_unsent_closed only returns closed-not-summarized ────
def test_unsent_closed():
    print("\n[TEST 2] unsent_closed filter")
    market_tracker._markets.clear()

    # Open market — not closed yet
    market_tracker.observe("a", "Q1", 0.5, 0.5, 10)

    # Closed market, summary not sent
    market_tracker.observe("b", "Q2", 0.5, 0.5, 10)
    market_tracker.mark_closed("b")

    # Closed market, summary already sent
    market_tracker.observe("c", "Q3", 0.5, 0.5, 10)
    market_tracker.mark_closed("c")
    market_tracker.mark_summary_sent("c")

    unsent = market_tracker.get_unsent_closed()
    ids = [m["id"] for m in unsent]
    assert "a" not in ids, "open market should not appear"
    assert "b" in ids, "closed unsent market should appear"
    assert "c" not in ids, "closed-already-sent should not appear"
    print(f"  ✓ unsent_closed = {ids}")
    print("  ✅ PASS")


# ─── Test 3: hourly stats aggregate ───────────────────────────
def test_hourly_stats():
    print("\n[TEST 3] hourly stats aggregation")
    market_tracker._markets.clear()

    # Five markets — 2 traded (1 win, 1 loss), 3 missed
    market_tracker.observe("m1", "Q1", 0.18, 0.82, 10)
    market_tracker.mark_entered("m1", "yes", 0.18, 10)
    market_tracker.mark_exited("m1", 0.30, 50, "TARGET_HIT", "win", 1.30)
    market_tracker.mark_closed("m1")

    market_tracker.observe("m2", "Q2", 0.20, 0.80, 15)
    market_tracker.mark_entered("m2", "yes", 0.20, 15)
    market_tracker.mark_exited("m2", 0.10, 215, "FORCE_CLOSE", "loss", -1.00)
    market_tracker.mark_closed("m2")

    market_tracker.observe("m3", "Q3", 0.40, 0.60, 5)
    market_tracker.mark_skipped("m3", market_tracker.REASON_NO_CHEAP_SIDE)
    market_tracker.mark_closed("m3")

    market_tracker.observe("m4", "Q4", 0.18, 0.82, 5)
    market_tracker.mark_skipped("m4", market_tracker.REASON_GLOBAL_LOCK)
    market_tracker.mark_closed("m4")

    market_tracker.observe("m5", "Q5", 0.18, 0.82, 100)  # past deadline
    market_tracker.mark_skipped("m5", market_tracker.REASON_PAST_ENTRY_DEADLINE)
    market_tracker.mark_closed("m5")

    stats = market_tracker.stats_since(hours=1.0)
    assert stats["markets"] == 5
    assert stats["traded"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["pnl"] == 0.30  # 1.30 - 1.00
    # cheap side seen: m1, m2, m4, m5 had ≤0.20 → 4 of 5
    assert stats["cheap_side_seen"] == 4

    reasons = stats["skip_reasons"]
    assert reasons.get("no_side_under_20c") == 1
    assert reasons.get("another_position_open") == 1
    assert reasons.get("past_entry_deadline") == 1

    print(f"  ✓ markets={stats['markets']} traded={stats['traded']} W/L={stats['wins']}/{stats['losses']}")
    print(f"  ✓ pnl=${stats['pnl']:+.2f}, cheap_side_seen={stats['cheap_side_seen']}")
    print(f"  ✓ skip reasons: {reasons}")
    print("  ✅ PASS")


# ─── Test 4: notifier.market_summary doesn't crash on either path ────
def test_notifier_summary():
    print("\n[TEST 4] notifier.market_summary smoke test")
    from notifier import notifier
    sent = []
    notifier.send = lambda msg, silent=False: sent.append(msg) or True

    # Entered + win
    notifier.market_summary(
        question="Bitcoin Up or Down — 10:00–10:05 AM ET",
        entered=True,
        entry_side="no", entry_price=0.18, entry_elapsed=20,
        exit_price=0.32, exit_trigger="TARGET_HIT",
        exit_outcome="win", exit_pnl=1.56,
        min_yes=0.82, min_no=0.18,
        min_yes_at=20, min_no_at=20,
        tick_count=240,
    )
    assert "TRADED (WIN)" in sent[-1]
    assert "0.180" in sent[-1] and "0.320" in sent[-1]
    print("  ✓ entered+win path renders")

    # Not entered, no cheap side seen
    notifier.market_summary(
        question="Bitcoin Up or Down — 10:05–10:10 AM ET",
        entered=False,
        min_yes=0.45, min_no=0.55,
        min_yes_at=120, min_no_at=120,
        skip_reason="no_side_under_20c",
        tick_count=300,
    )
    assert "NO TRADE" in sent[-1]
    assert "No side reached" in sent[-1] or "no_side_under_20c" in sent[-1]
    print("  ✓ no-trade + no-cheap-side path renders")

    # Not entered, global lock
    notifier.market_summary(
        question="Bitcoin Up or Down — 10:10–10:15 AM ET",
        entered=False,
        min_yes=0.18, min_no=0.82,
        min_yes_at=10, min_no_at=10,
        skip_reason="another_position_open",
        tick_count=300,
    )
    assert "Another position" in sent[-1] or "global" in sent[-1].lower()
    print("  ✓ no-trade + global-lock path renders")

    print(f"  ✓ sent {len(sent)} messages without errors")
    print("  ✅ PASS")


# ─── Test 5: notifier.hourly_digest renders with traded + missed ────
def test_notifier_digest():
    print("\n[TEST 5] notifier.hourly_digest smoke test")
    from notifier import notifier
    sent = []
    notifier.send = lambda msg, silent=False: sent.append(msg) or True

    notifier.hourly_digest({
        "markets": 12,
        "traded": 3,
        "wins": 2,
        "losses": 1,
        "pnl": 1.86,
        "cheap_side_seen": 5,
        "skip_reasons": {
            "another_position_open": 2,
            "no_side_under_20c": 7,
        },
    })
    msg = sent[-1]
    assert "12" in msg  # markets observed
    assert "3" in msg   # trades taken
    assert "2/3" in msg or "Win rate" in msg
    assert "1.86" in msg
    print("  ✓ digest renders with all sections")
    print("  ✅ PASS")


if __name__ == "__main__":
    test_lifecycle()
    test_unsent_closed()
    test_hourly_stats()
    test_notifier_summary()
    test_notifier_digest()
    print("\n" + "=" * 60)
    print("  🎉 ALL TRACKER TESTS PASSED")
    print("=" * 60)
