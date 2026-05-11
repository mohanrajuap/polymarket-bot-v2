"""
End-to-end simulation of the BTC 5-min strategy.
Mocks the Polymarket API so we can verify the strategy logic without network.

Run:
    cd polymarket-bot-v2-main
    TRADING_MODE=paper DATA_DIR=/tmp/arena_sim python3 simulate.py
"""

import os
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("DATA_DIR", "/tmp/arena_sim")

import json
import shutil
import logging
import pathlib
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

# Wipe and re-init the test DB so each run starts clean
data_dir = pathlib.Path(os.environ["DATA_DIR"])
if data_dir.exists():
    shutil.rmtree(data_dir)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("sim")

import db
db.init_db()

import config
from constants import (
    ENTRY_PRICE_MAX,
    EXIT_PRICE_TARGET,
    EXIT_STOP_LOSS,
    ENTRY_WINDOW_END_SEC,
    EXIT_WINDOW_END_SEC,
    WINDOW_DURATION_SEC,
    PAPER_TRADE_SIZE_USD,
)

# ─── Mock state we control during the simulation ─────────────
# A timeline of what the Polymarket API "returns" for our fake market.
SIM_MARKET_ID = "sim-market-001"
SIM_TIME_OFFSET_SEC = 0       # advanced by the test
SIM_YES_PRICE = 0.50          # set by each test scenario
SIM_NO_PRICE = 0.50
SIM_RESOLVED = False
SIM_WINNER = "yes"

SIM_START_DT = datetime.now(timezone.utc)


def fake_market_dict():
    """Builds the Gamma-API-shaped market the scanner / exit_monitor expect."""
    # Window OPEN time = now - SIM_TIME_OFFSET_SEC
    # Window CLOSE time = open + 300s; slug embeds close ts
    open_dt = SIM_START_DT - timedelta(seconds=SIM_TIME_OFFSET_SEC)
    close_dt = open_dt + timedelta(seconds=300)
    close_ts = int(close_dt.timestamp())

    return {
        "id": SIM_MARKET_ID,
        "conditionId": SIM_MARKET_ID,
        "question": "Bitcoin Up or Down - May 9, 5:50AM-5:55AM ET",
        "slug": f"btc-updown-5m-{close_ts}",
        "active": True,
        "closed": SIM_RESOLVED,
        "startDate": open_dt.isoformat().replace("+00:00", "Z"),
        "endDate": close_dt.isoformat().replace("+00:00", "Z"),
        "outcomePrices": json.dumps([f"{SIM_YES_PRICE}", f"{SIM_NO_PRICE}"]),
        "clobTokenIds": json.dumps(["yes-token-001", "no-token-001"]),
        "tokens": [
            {"outcome": "Yes", "token_id": "yes-token-001", "winner": SIM_RESOLVED and SIM_WINNER == "yes"},
            {"outcome": "No",  "token_id": "no-token-001",  "winner": SIM_RESOLVED and SIM_WINNER == "no"},
        ],
    }


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_get(url, *args, **kwargs):
    """
    Intercept all requests.get() calls.
    /markets             → list with our one fake market
    /markets?slug=...    → list with that single market (slug-based lookup)
    /markets/<id>        → single fake market
    binance/yahoo        → tiny stubs so the BTC crash monitor doesn't blow up
    """
    # Slug-based lookup: GET /markets?slug=...
    params = kwargs.get("params") or {}
    if (
        "gamma-api.polymarket.com/markets" in url
        and url.endswith("/markets")
        and "slug" in params
    ):
        # Return our fake market for ANY slug query so the slug-first scanner
        # finds it. Real Polymarket would only match the actual slug.
        return FakeResponse([fake_market_dict()])

    if "gamma-api.polymarket.com/markets" in url and url.endswith("/markets"):
        return FakeResponse([fake_market_dict()])
    if "gamma-api.polymarket.com/markets/" in url:
        return FakeResponse(fake_market_dict())
    if "binance.com" in url:
        return FakeResponse([{"symbol": "BTCUSDT", "price": "100000.00"}])
    if "query1.finance.yahoo.com" in url:
        return FakeResponse({"chart": {"result": [{"meta": {"regularMarketPrice": 100.0}}]}})
    return FakeResponse({}, status=404)


# Patch requests.get for the whole simulation
patcher = patch("requests.get", side_effect=fake_get)
patcher.start()

# Now safe to import the rest
import main
from scanner import scanner
from executor import executor
from exit_monitor import exit_monitor
from execution.polymarket_client import polymarket_client

# Also patch the polymarket client's internal session.get
# (we couldn't do this earlier because the client didn't exist yet)
session_patcher = patch.object(polymarket_client._session, "get", side_effect=fake_get)
session_patcher.start()


# ─── Helpers ────────────────────────────────────────────────
def reset_db():
    """Remove all positions/trades between scenarios."""
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM trades")
    c.execute("DELETE FROM open_positions")
    c.execute("DELETE FROM conditions")
    conn.commit()
    conn.close()
    # Reset scanner cache so it re-fetches
    scanner._market_cache = []
    scanner._last_market_fetch = 0
    scanner._radar = []


def set_market(yes_price, no_price, time_elapsed_sec, resolved=False, winner="yes"):
    """Move the simulation clock + set side prices."""
    global SIM_YES_PRICE, SIM_NO_PRICE, SIM_TIME_OFFSET_SEC, SIM_RESOLVED, SIM_WINNER
    SIM_YES_PRICE = yes_price
    SIM_NO_PRICE = no_price
    SIM_TIME_OFFSET_SEC = time_elapsed_sec
    SIM_RESOLVED = resolved
    SIM_WINNER = winner


def divider(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


# ─── Scenarios ──────────────────────────────────────────────

def scenario_1_profit_take():
    divider("SCENARIO 1: ENTRY + PROFIT TAKE @ 30¢ inside window")
    reset_db()
    bots = main.create_bots()

    # t=10s, NO is at 18¢ → bot should buy
    set_market(yes_price=0.82, no_price=0.18, time_elapsed_sec=10)
    opps = scanner.scan()
    print(f"  scanner found {len(opps)} opportunities")
    assert opps, "scanner should have found the cheap NO side"
    assert opps[0]["side"] == "no"
    assert opps[0]["price"] == 0.18

    # Run main's per-tick decision logic by hand (one bot will take it)
    for bot in bots:
        if bot.can_trade(opps[0]):
            decision = {
                "side": opps[0]["side"],
                "suggested_amount": bot.get_amount(),
                "bot_name": bot.name,
                "confidence": 0.5,
                "edge": EXIT_PRICE_TARGET - opps[0]["price"],
                "reasoning": "test",
            }
            mkt = {
                "id": opps[0]["id"],
                "question": opps[0]["question"],
                "price_yes": opps[0]["price"],
                "price_no": opps[0]["price"],
                "token_id": opps[0]["token_id"],
            }
            r = executor.execute(decision, mkt)
            print(f"  {bot.name} → executor: {r.get('success')}, price={r.get('price')}")
            bot.mark_traded(opps[0]["id"])
            break

    open_pos = db.get_open_positions()
    print(f"  open positions after entry: {len(open_pos)}")
    assert len(open_pos) == 1
    pos = open_pos[0]
    print(f"  bought {pos['side'].upper()} ${pos['amount']} @ {pos['entry_price']:.3f}")

    # t=60s, NO climbs to 32¢ — should profit-take
    set_market(yes_price=0.68, no_price=0.32, time_elapsed_sec=60)
    exit_monitor.check_all_positions()

    open_pos_after = db.get_open_positions()
    print(f"  open positions after price hit 32¢: {len(open_pos_after)}")
    assert len(open_pos_after) == 0, "position should have been closed by profit-take"

    # Inspect the resolved trade
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1")
    t = dict(c.fetchone())
    conn.close()
    print(f"  resolved trade: outcome={t['outcome']} pnl=${t['pnl']:+.2f} exit={t['exit_price']:.3f}")
    assert t["outcome"] == "win"
    assert t["pnl"] > 0
    print("  ✅ PASS")


def scenario_2_no_entry_outside_window():
    divider("SCENARIO 2: ENTRY WINDOWS — standard bots vs late-entry bot")
    reset_db()
    bots = main.create_bots()

    # ── 2a. t=120s, price 15¢ — standard bots reject, late-entry takes it
    set_market(yes_price=0.85, no_price=0.15, time_elapsed_sec=120)
    opps = scanner.scan()
    assert opps, "scanner should now surface this (late-entry bot may want it)"
    print(f"  scanner found {len(opps)} opp at t=120s")

    eligible = [b.name for b in bots if b.can_trade(opps[0])]
    print(f"  eligible bots: {eligible}")
    assert eligible == ["btc-late-entry"], \
        f"only late-entry should accept past 90s, got {eligible}"

    # ── 2b. t=220s, past force-close — nobody enters
    reset_db()
    bots = main.create_bots()
    set_market(yes_price=0.85, no_price=0.15, time_elapsed_sec=220)
    opps2 = scanner.scan()
    print(f"  scanner found {len(opps2)} opp at t=220s (past force-close)")
    assert len(opps2) == 0, "scanner should reject past force-close"
    print("  ✅ PASS")


def scenario_3_force_close_at_210s():
    divider("SCENARIO 3: ENTERED EARLY, PRICE STILL UNDER 30¢ AT t=210s → FORCE CLOSE")
    reset_db()
    bots = main.create_bots()

    # t=20s, YES at 18¢ → buy
    set_market(yes_price=0.18, no_price=0.82, time_elapsed_sec=20)
    opps = scanner.scan()
    assert opps and opps[0]["side"] == "yes"
    for bot in bots:
        if bot.can_trade(opps[0]):
            executor.execute(
                {"side": "yes", "suggested_amount": 2.0, "bot_name": bot.name,
                 "confidence": 0.5, "edge": 0.12, "reasoning": "test"},
                {"id": opps[0]["id"], "question": opps[0]["question"],
                 "price_yes": 0.18, "price_no": 0.82, "token_id": opps[0]["token_id"]},
            )
            bot.mark_traded(opps[0]["id"])
            break

    # t=120s — price moves up to 25¢ (not yet 30¢) → no trigger
    set_market(yes_price=0.25, no_price=0.75, time_elapsed_sec=120)
    exit_monitor.check_all_positions()
    assert len(db.get_open_positions()) == 1, "should NOT close — under 30¢ and before force-close"

    # t=200s — still under 30¢ → still hold (force close is 210s)
    set_market(yes_price=0.27, no_price=0.73, time_elapsed_sec=200)
    exit_monitor.check_all_positions()
    assert len(db.get_open_positions()) == 1, "should NOT close at 200s — before 210s deadline"

    # t=210s — price is 25¢, force-close triggers no matter what
    set_market(yes_price=0.25, no_price=0.75, time_elapsed_sec=210)
    exit_monitor.check_all_positions()

    open_after = db.get_open_positions()
    print(f"  positions open after t=210s force-close: {len(open_after)}")
    assert len(open_after) == 0, "force-close MUST fire at t≥210s"

    conn = db.get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1")
    t = dict(c.fetchone())
    conn.close()
    print(f"  resolved: outcome={t['outcome']} pnl=${t['pnl']:+.2f} exit={t['exit_price']:.3f}")
    # Bought YES @ 0.18, force-closed @ 0.25 → small win
    assert t["outcome"] == "win"
    assert t["pnl"] > 0
    print("  ✅ PASS — force-close took the small profit at deadline")


def scenario_3b_force_close_loss():
    divider("SCENARIO 3B: PRICE TANKS BELOW ENTRY → force close at loss at t=210s")
    reset_db()
    bots = main.create_bots()

    set_market(yes_price=0.18, no_price=0.82, time_elapsed_sec=10)
    opps = scanner.scan()
    for bot in bots:
        if bot.can_trade(opps[0]):
            executor.execute(
                {"side": "yes", "suggested_amount": 2.0, "bot_name": bot.name,
                 "confidence": 0.5, "edge": 0.12, "reasoning": "test"},
                {"id": opps[0]["id"], "question": opps[0]["question"],
                 "price_yes": 0.18, "price_no": 0.82, "token_id": opps[0]["token_id"]},
            )
            bot.mark_traded(opps[0]["id"])
            break

    # Force close with price at 5¢
    set_market(yes_price=0.05, no_price=0.95, time_elapsed_sec=215)
    exit_monitor.check_all_positions()

    conn = db.get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1")
    t = dict(c.fetchone())
    conn.close()
    print(f"  resolved: outcome={t['outcome']} pnl=${t['pnl']:+.2f} exit={t['exit_price']:.3f}")
    assert t["outcome"] == "loss"
    assert t["pnl"] < 0
    print("  ✅ PASS — force-close booked the loss")


def scenario_4_global_one_position_lock():
    divider("SCENARIO 4: GLOBAL POSITION LOCK — only ONE position open at a time")
    reset_db()
    bots = main.create_bots()

    # First bot enters
    set_market(yes_price=0.18, no_price=0.82, time_elapsed_sec=10)
    opps = scanner.scan()
    first_taker = None
    for bot in bots:
        if bot.can_trade(opps[0]):
            executor.execute(
                {"side": "yes", "suggested_amount": 2.0, "bot_name": bot.name,
                 "confidence": 0.5, "edge": 0.12, "reasoning": "test"},
                {"id": opps[0]["id"], "question": opps[0]["question"],
                 "price_yes": 0.18, "price_no": 0.82, "token_id": opps[0]["token_id"]},
            )
            bot.mark_traded(opps[0]["id"])
            first_taker = bot.name
            break
    print(f"  {first_taker} took the first trade")
    assert len(db.get_open_positions()) == 1

    # Pretend a different market also has a cheap side at the same time
    # — we mock a *new* market id
    global SIM_MARKET_ID
    saved_id = SIM_MARKET_ID
    SIM_MARKET_ID = "sim-market-002"
    scanner._market_cache = []
    scanner._last_market_fetch = 0

    set_market(yes_price=0.82, no_price=0.18, time_elapsed_sec=10)
    opps2 = scanner.scan()
    print(f"  second market shows up in scanner: {len(opps2) > 0}")

    # Even with this fresh opportunity, global cap should block ALL bots
    blocked = 0
    for bot in bots:
        if not bot.can_trade(opps2[0]):
            blocked += 1
    print(f"  bots blocked by global lock: {blocked}/{len(bots)}")
    assert blocked == len(bots), "all bots must be blocked while ANY position is open"

    # Restore for clean state
    SIM_MARKET_ID = saved_id
    print("  ✅ PASS")


def scenario_5_no_re_entry():
    divider("SCENARIO 5: BOT WON'T RE-ENTER A MARKET IT ALREADY TRADED")
    reset_db()
    bots = main.create_bots()

    set_market(yes_price=0.82, no_price=0.18, time_elapsed_sec=10)
    opps = scanner.scan()
    for bot in bots:
        if bot.can_trade(opps[0]):
            executor.execute(
                {"side": "no", "suggested_amount": 2.0, "bot_name": bot.name,
                 "confidence": 0.5, "edge": 0.12, "reasoning": "test"},
                {"id": opps[0]["id"], "question": opps[0]["question"],
                 "price_yes": 0.82, "price_no": 0.18, "token_id": opps[0]["token_id"]},
            )
            bot.mark_traded(opps[0]["id"])
            break

    # Profit-take and close
    set_market(yes_price=0.68, no_price=0.32, time_elapsed_sec=60)
    exit_monitor.check_all_positions()
    assert len(db.get_open_positions()) == 0

    # Same market dips again to 18¢ at t=80s → bot must NOT re-enter
    set_market(yes_price=0.82, no_price=0.18, time_elapsed_sec=80)
    opps2 = scanner.scan()
    print(f"  scanner still sees opportunity (correct): {len(opps2) > 0}")
    blocked = 0
    for bot in bots:
        if not bot.can_trade(opps2[0]):
            blocked += 1
    print(f"  bots blocked from re-entering: {blocked}/{len(bots)}")
    assert blocked == len(bots), "all bots should refuse re-entry into same market"
    print("  ✅ PASS")


def scenario_6_stop_loss_disabled():
    divider("SCENARIO 6: STOP LOSS DISABLED — price drops, bot still holds")
    reset_db()
    bots = main.create_bots()

    set_market(yes_price=0.18, no_price=0.82, time_elapsed_sec=10)
    opps = scanner.scan()
    for bot in bots:
        if bot.can_trade(opps[0]):
            executor.execute(
                {"side": "yes", "suggested_amount": 2.0, "bot_name": bot.name,
                 "confidence": 0.5, "edge": 0.12, "reasoning": "test"},
                {"id": opps[0]["id"], "question": opps[0]["question"],
                 "price_yes": 0.18, "price_no": 0.82, "token_id": opps[0]["token_id"]},
            )
            bot.mark_traded(opps[0]["id"])
            break

    # Price tanks to 3¢ at t=60s
    set_market(yes_price=0.03, no_price=0.97, time_elapsed_sec=60)
    exit_monitor.check_all_positions()

    open_pos = db.get_open_positions()
    print(f"  EXIT_STOP_LOSS = {EXIT_STOP_LOSS} → positions still open: {len(open_pos)}")
    assert EXIT_STOP_LOSS == 0.0
    assert len(open_pos) == 1, "stop loss is disabled by default → must hold"
    print("  ✅ PASS")


# ─── Run all scenarios ───────────────────────────────────────
if __name__ == "__main__":
    log.info(f"Strategy: entry ≤{ENTRY_PRICE_MAX:.2f}, exit ≥{EXIT_PRICE_TARGET:.2f}")
    log.info(f"Entry deadline {ENTRY_WINDOW_END_SEC}s, force-close {EXIT_WINDOW_END_SEC}s, expiry {WINDOW_DURATION_SEC}s")
    log.info(f"Trade size: ${PAPER_TRADE_SIZE_USD}, stop loss: {'ON' if EXIT_STOP_LOSS > 0 else 'OFF'}")

    scenario_1_profit_take()
    scenario_2_no_entry_outside_window()
    scenario_3_force_close_at_210s()
    scenario_3b_force_close_loss()
    scenario_4_global_one_position_lock()
    scenario_5_no_re_entry()
    scenario_6_stop_loss_disabled()

    print()
    print("=" * 70)
    print("  🎉 ALL SCENARIOS PASSED")
    print("=" * 70)

    patcher.stop()
