"""
Polymarket BTC 5-Minute Bot

Strategy:
  - Find BTC 5-minute Up/Down markets
  - In the first 1:30 of the window (5:00 → 3:30 remaining), buy a side
    whose price is below 20¢
  - Watch the price every second; if it climbs to 30¢ before 3:30 remaining,
    sell for profit
  - Otherwise, hold to expiry and resolve naturally

Each bot has its own taste (yes-only, no-only, either-side).
Reuses existing Telegram notifier, dashboard, circuit breaker, and DB layer.
"""

import logging
import logging.handlers
import time
import threading
import uvicorn
import os
import sys
import base64
import requests

# Set up logging — stdout for live view, rotating file for permanent record
log_dir = os.environ.get("DATA_DIR", "/tmp/arena") + "/logs"
os.makedirs(log_dir, exist_ok=True)

_handlers = [logging.StreamHandler(sys.stdout)]
try:
    _file_handler = logging.handlers.RotatingFileHandler(
        f"{log_dir}/bot.log",
        maxBytes=10_000_000,  # 10 MB per file
        backupCount=5,         # keep 5 rolled files = 50 MB total
    )
    _handlers.append(_file_handler)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger("main")

import db
import config

db.init_db()

from scanner import scanner
from executor import executor
from exit_monitor import exit_monitor
from notifier import notifier
from risk.circuit_breaker import circuit_breaker
from dashboard.server import app, set_refs
from market_tracker import market_tracker
from constants import (
    PAPER_TRADE_SIZE_USD,
    PRICE_POLL_INTERVAL_SEC,
    SCAN_INTERVAL_SECONDS,
    ENTRY_PRICE_MAX,
    EXIT_PRICE_TARGET,
    ENTRY_WINDOW_END_SEC,
    FORCE_CLOSE_SEC,
)
polymarket_client = None  # set inside trading_loop on first run


MAX_TRADE_AMOUNT = 5.0    # safety cap per trade ($2 strategy, 5x headroom)
MAX_OPEN_GLOBAL = 1       # ONE open position at a time across ALL bots


# ─── BTC 5-MIN BOTS ──────────────────────────────────────────
class BTC5MinBot:
    """
    A bot with a side preference and its own entry-window deadline:
      - side_preference: "yes" | "no" | "either"
      - entry_window_end_sec: latest second after market open this bot will
        enter. Default = ENTRY_WINDOW_END_SEC (90s = 3:30 remaining).
        A "late entry" bot may have this set to FORCE_CLOSE_SEC (210s =
        1:30 remaining), letting it pick up opportunities that appear after
        the standard 1:30 window.
    """

    def __init__(self, name: str, side_preference: str, fixed_amount: float,
                 entry_window_end_sec: int = ENTRY_WINDOW_END_SEC):
        assert side_preference in ("yes", "no", "either")
        self.name = name
        self.side_preference = side_preference
        self.fixed_amount = fixed_amount
        self.entry_window_end_sec = entry_window_end_sec
        self._active = True
        self._traded_market_ids: set[str] = set()
        self.trades_today = 0

    def can_trade(self, opp: dict) -> bool:
        if not self._active:
            return False

        # Side filter
        if self.side_preference != "either" and opp["side"] != self.side_preference:
            return False

        # Per-bot entry window — the scanner surfaces every cheap-side
        # opportunity up to FORCE_CLOSE_SEC; this bot only acts on opps
        # that arrived strictly inside ITS window. We use `>=` here so that
        # at exactly t=entry_window_end_sec we already refuse — otherwise
        # the trade would force-close on the same tick.
        elapsed = opp.get("seconds_elapsed")
        if elapsed is None or elapsed >= self.entry_window_end_sec:
            return False

        # No re-entry into the same market — BY ANY BOT.
        # Once one bot has traded a market in this run, we don't pile on.
        market_id = opp.get("id", "")
        if market_id and market_id in self._traded_market_ids:
            return False
        # Also check the DB so a bot restart can't re-enter a market we already
        # traded in the last hour.
        if market_id:
            try:
                conn = db.get_conn()
                c = conn.cursor()
                c.execute("""
                    SELECT 1 FROM trades
                    WHERE market_id = ?
                      AND created_at >= datetime('now', '-1 hour')
                    LIMIT 1
                """, (market_id,))
                if c.fetchone() is not None:
                    conn.close()
                    self._traded_market_ids.add(market_id)
                    return False
                conn.close()
            except Exception:
                pass

        # GLOBAL position cap — only one trade open at a time across all bots
        try:
            open_positions = db.get_open_positions()
            if len(open_positions) >= MAX_OPEN_GLOBAL:
                return False
        except Exception:
            pass

        # Sanity-check the price is actually in entry range
        if opp["price"] > ENTRY_PRICE_MAX:
            return False

        return True

    def get_amount(self) -> float:
        return round(min(self.fixed_amount, MAX_TRADE_AMOUNT), 2)

    def stop(self):
        self._active = False
        logger.info(f"🔴 {self.name} stopped")

    def start(self):
        self._active = True
        logger.info(f"🟢 {self.name} started")

    def mark_traded(self, market_id: str):
        if market_id:
            self._traded_market_ids.add(market_id)
        self.trades_today += 1

    def get_status(self) -> dict:
        perf = db.get_bot_performance(self.name, hours=24)
        return {
            "name": self.name,
            "active": self._active,
            "side_preference": self.side_preference,
            "fixed_amount": self.fixed_amount,
            "entry_window_end_sec": self.entry_window_end_sec,
            "current_trade_size": self.get_amount(),
            "trades_today": self.trades_today,
            "win_rate": perf.get("win_rate", 0),
            "total_trades": perf.get("total_trades", 0),
            "pnl": perf.get("total_pnl", 0),
            # Legacy fields the dashboard may read
            "min_gap": 0,
            "max_no_price": ENTRY_PRICE_MAX,
            "position_pct": 0,
        }


def create_bots():
    return [
        BTC5MinBot("btc-yes-only",    side_preference="yes",
                   fixed_amount=PAPER_TRADE_SIZE_USD,
                   entry_window_end_sec=ENTRY_WINDOW_END_SEC),
        BTC5MinBot("btc-no-only",     side_preference="no",
                   fixed_amount=PAPER_TRADE_SIZE_USD,
                   entry_window_end_sec=ENTRY_WINDOW_END_SEC),
        BTC5MinBot("btc-either-side", side_preference="either",
                   fixed_amount=PAPER_TRADE_SIZE_USD,
                   entry_window_end_sec=ENTRY_WINDOW_END_SEC),
        # Late-entry bot: same logic, but accepts entries up until
        # FORCE_CLOSE_SEC (210s = 1:30 remaining). Catches opportunities
        # that arrive after the standard 1:30 window.
        BTC5MinBot("btc-late-entry",  side_preference="either",
                   fixed_amount=PAPER_TRADE_SIZE_USD,
                   entry_window_end_sec=FORCE_CLOSE_SEC),
    ]


# ─── BTC CRASH MONITOR (kept from the original) ──────────────
def monitor_btc_crash(bots):
    """Emergency stop if BTC spot crashes >7% from session entry price."""
    entry_price = 0
    while True:
        try:
            prices = scanner.fetch_all_prices()
            current = prices.get("BTC", 0)

            if entry_price == 0 and current > 0:
                entry_price = current

            if entry_price > 0 and current > 0:
                drop = (entry_price - current) / entry_price
                if drop > 0.07:
                    logger.warning(f"🚨 BTC dropped {drop*100:.1f}%! Emergency stop!")
                    circuit_breaker.trip(f"BTC crashed {drop*100:.1f}%")
                    for bot in bots:
                        bot.stop()
                    notifier.circuit_breaker_tripped(f"BTC crashed {drop*100:.1f}%")
                    break
                elif drop > 0.05:
                    logger.warning(f"⚠️ BTC down {drop*100:.1f}% - approaching danger zone")

            time.sleep(60)
        except Exception as e:
            logger.error(f"BTC crash monitor error: {e}")
            time.sleep(60)


# ─── BACKUP / RESTORE (kept from the original) ───────────────
def backup_to_github():
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        return
    db_path = str(config.DB_PATH)
    if not os.path.exists(db_path):
        return
    try:
        with open(db_path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        r = requests.get(
            f"https://api.github.com/repos/{repo}/contents/data/arena.db",
            headers={"Authorization": f"token {token}"}, timeout=10,
        )
        sha = r.json().get("sha", "") if r.status_code == 200 else ""
        requests.put(
            f"https://api.github.com/repos/{repo}/contents/data/arena.db",
            headers={"Authorization": f"token {token}"},
            json={"message": "auto backup", "content": content, "sha": sha},
            timeout=15,
        )
        logger.info("✅ DB backed up to GitHub")
    except Exception as e:
        logger.error(f"Backup error: {e}")


def restore_from_github():
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        return
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/contents/data/arena.db",
            headers={"Authorization": f"token {token}"}, timeout=10,
        )
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"])
            db_path = str(config.DB_PATH)
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            with open(db_path, "wb") as f:
                f.write(content)
            logger.info("✅ DB restored from GitHub")
    except Exception as e:
        logger.error(f"Restore error: {e}")


# ─── STARTUP HELPERS ─────────────────────────────────────────
def send_existing_trades_summary():
    """Telegram summary of any positions still open at startup."""
    try:
        positions = db.get_open_positions()
        if not positions:
            return

        lines = ["📋 OPEN POSITIONS AT STARTUP", "=" * 28]
        total = 0.0
        for p in positions:
            q = str(p.get("market_question") or "")[:60]
            amt = float(p.get("amount", 0))
            bot = str(p.get("bot_name", ""))
            entry = float(p.get("entry_price", 0))
            side = str(p.get("side", "")).upper()
            total += amt
            lines.append(q)
            lines.append(f"  Bot:{bot} {side} Stake:${round(amt,2)} Entry:{round(entry,3)}")
        lines.append("=" * 28)
        lines.append(f"Total staked: ${round(total,2)}")
        notifier.send(chr(10).join(lines))
    except Exception as e:
        logger.error("send_existing_trades_summary error: " + str(e))


def restore_traded_market_ids(bots):
    """Prevent re-trading the same market after a restart (within last hour)."""
    try:
        conn = db.get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT market_id FROM trades
            WHERE created_at >= datetime('now', '-1 hour')
        """)
        ids = {r["market_id"] for r in c.fetchall() if r["market_id"]}
        conn.close()
        if ids:
            for bot in bots:
                bot._traded_market_ids.update(ids)
            logger.info(f"✅ Restored {len(ids)} recently-traded market IDs")
    except Exception as e:
        logger.error(f"restore_traded_market_ids error: {e}")


# ─── MAIN TRADING LOOP ───────────────────────────────────────

# Mutable state for the trading loop's periodic logging (shared between calls)
# [0] = total ticks processed, [1] = unix-ts of last status line
tick_counter = [0, 0.0]


def _why_cant_trade(bot, opp: dict) -> str:
    """Re-run can_trade's gates and return the FIRST one that rejected.

    Used only for log diagnostics, never for trading decisions.
    """
    if not bot._active:
        return "bot inactive"
    if bot.side_preference != "either" and opp["side"] != bot.side_preference:
        return f"side mismatch (wants {bot.side_preference}, opp is {opp['side']})"
    elapsed = opp.get("seconds_elapsed")
    if elapsed is None:
        return "elapsed=None"
    if elapsed >= bot.entry_window_end_sec:
        return f"past bot window ({elapsed}s >= {bot.entry_window_end_sec}s)"
    market_id = opp.get("id", "")
    if market_id and market_id in bot._traded_market_ids:
        return "already traded (in-memory)"
    if market_id:
        try:
            conn = db.get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT 1 FROM trades WHERE market_id=? AND created_at >= datetime('now','-1 hour') LIMIT 1",
                (market_id,),
            )
            row = c.fetchone()
            conn.close()
            if row is not None:
                return "already traded (DB)"
        except Exception as e:
            return f"db check failed: {e}"
    try:
        open_pos = db.get_open_positions()
        if len(open_pos) >= MAX_OPEN_GLOBAL:
            names = [p.get("bot_name", "?") for p in open_pos]
            return f"global lock — {names}"
    except Exception as e:
        return f"db lookup failed: {e}"
    if opp["price"] > ENTRY_PRICE_MAX:
        return f"price too high ({opp['price']:.3f} > {ENTRY_PRICE_MAX:.2f})"
    return "unknown — can_trade returned False but all gates pass (BUG?)"


def _log_periodic_status():
    """Print a single-line status pulse every ~30s with what the bot is seeing."""
    try:
        from market_tracker import market_tracker
        recent = market_tracker.recent(n=3)
        open_pos = db.get_open_positions()
        balance = polymarket_client.get_balance() if polymarket_client else 0.0

        # Build a compact view of the most recent markets
        market_lines = []
        for m in recent:
            mid_short = m["id"][:12]
            min_yes = m["min_yes_price"]
            min_no = m["min_no_price"]
            cheapest = min(min_yes, min_no)
            ts = "✓ entered" if m["entered"] else f"⏸ {m.get('skip_reason') or 'no_match'}"
            market_lines.append(
                f"      [{mid_short}] yes_min={min_yes:.3f} no_min={min_no:.3f} "
                f"cheapest={cheapest:.3f} ticks={m['tick_count']} → {ts}"
            )

        logger.info(
            f"📡 STATUS  open_positions={len(open_pos)}  balance=${balance:.2f}  "
            f"recent_markets={len(recent)}"
        )
        for line in market_lines:
            logger.info(line)
    except Exception as e:
        logger.debug(f"periodic status error: {e}")


def _send_market_summary(m: dict):
    """Send one Telegram per closed market — entered or not."""
    try:
        notifier.market_summary(
            question=m.get("question", "")[:80],
            entered=m.get("entered", False),
            entry_side=m.get("entry_side"),
            entry_price=m.get("entry_price"),
            entry_elapsed=m.get("entry_elapsed"),
            exit_price=m.get("exit_price"),
            exit_trigger=m.get("exit_trigger"),
            exit_outcome=m.get("exit_outcome"),
            exit_pnl=m.get("exit_pnl"),
            min_yes=m.get("min_yes_price"),
            min_no=m.get("min_no_price"),
            min_yes_at=m.get("min_yes_at_elapsed"),
            min_no_at=m.get("min_no_at_elapsed"),
            skip_reason=m.get("skip_reason"),
            tick_count=m.get("tick_count", 0),
        )
    except Exception as e:
        logger.debug(f"market summary error: {e}")


def _send_hourly_digest():
    """Send a Telegram digest once an hour with aggregate stats."""
    try:
        from market_tracker import market_tracker
        stats = market_tracker.stats_since(hours=1.0)
        if stats.get("markets", 0) == 0:
            return  # nothing to report
        notifier.hourly_digest(stats)
    except Exception as e:
        logger.debug(f"hourly digest error: {e}")


def trading_loop(bots):
    logger.info("🚀 BTC 5-min trading loop started")

    # Lazy import to make sure constants are loaded
    from market_tracker import market_tracker
    from execution.polymarket_client import polymarket_client as pc
    global polymarket_client
    polymarket_client = pc

    while True:
        try:
            # Circuit breaker
            if not circuit_breaker.check():
                logger.warning("Circuit breaker open, sleeping 60s...")
                time.sleep(60)
                continue

            opportunities = scanner.scan()

            if opportunities:
                logger.info(
                    f"🔎 {len(opportunities)} BTC 5-min opportunit"
                    f"{'y' if len(opportunities)==1 else 'ies'} in entry window:"
                )
                for o in opportunities:
                    logger.info(
                        f"    • {o['side'].upper()} @ {o['price']:.3f}  "
                        f"t={o['seconds_elapsed']}s  market={o['id'][:12]}..."
                    )

            for opp in opportunities:
                # Try each bot in order; first eligible bot takes the trade
                taken = False
                first_skip_reason = None
                elapsed = opp.get("seconds_elapsed", 0)
                # If this opportunity is past EVERY bot's own entry window,
                # it's effectively past the entry deadline for the team.
                # Use >= to match can_trade()'s half-open window.
                max_bot_window = max((b.entry_window_end_sec for b in bots), default=ENTRY_WINDOW_END_SEC)
                if elapsed >= max_bot_window:
                    first_skip_reason = market_tracker.REASON_PAST_ENTRY_DEADLINE
                    logger.info(
                        f"    ↳ past max bot window ({elapsed}s >= {max_bot_window}s) — skip"
                    )

                for bot in bots:
                    can = bot.can_trade(opp)
                    if not can:
                        # Why? Be explicit so we can debug
                        reason = _why_cant_trade(bot, opp)
                        logger.info(f"    ↳ {bot.name} can_trade=False ({reason})")
                        if first_skip_reason is None:
                            try:
                                open_positions = db.get_open_positions()
                                if len(open_positions) >= MAX_OPEN_GLOBAL:
                                    first_skip_reason = market_tracker.REASON_GLOBAL_LOCK
                                elif opp["id"] in bot._traded_market_ids:
                                    first_skip_reason = market_tracker.REASON_ALREADY_TRADED
                            except Exception:
                                pass
                        continue

                    logger.info(f"    ↳ {bot.name} can_trade=TRUE — executing")

                    amount = bot.get_amount()
                    decision = {
                        "action": "buy",
                        "side": opp["side"],
                        "confidence": 0.5,  # neutral; this strategy is price-based
                        "suggested_amount": amount,
                        "reasoning": (
                            f"BTC 5-min: {opp['side'].upper()} at {opp['price']:.3f} "
                            f"(t={opp['seconds_elapsed']}s, "
                            f"{opp['seconds_to_window_end']}s left in entry window)"
                        ),
                        "bot_name": bot.name,
                        "edge": EXIT_PRICE_TARGET - opp["price"],
                    }

                    market_for_executor = {
                        "id": opp["id"],
                        "question": opp["question"],
                        "price_yes": opp["price_yes"],
                        "price_no": opp["price_no"],
                        "token_id": opp["token_id"],
                    }
                    market_for_executor["price_yes"] = opp["price"]

                    result = executor.execute(decision, market_for_executor)

                    if result.get("success"):
                        bot.mark_traded(opp["id"])
                        market_tracker.mark_entered(
                            opp["id"], opp["side"],
                            float(result.get("price") or opp["price"]),
                            opp["seconds_elapsed"],
                        )
                        taken = True
                        logger.info(
                            f"✅ [{bot.name}] BUY {opp['side'].upper()} "
                            f"${amount:.2f} @ {opp['price']:.3f} → {opp['question'][:50]}"
                        )
                        try:
                            notifier.trade_placed(
                                bot_name=bot.name,
                                side=opp["side"],
                                amount=amount,
                                market=opp["question"],
                                confidence=0.5,
                                gap_pct=(EXIT_PRICE_TARGET - opp["price"]) * 100,
                                yield_pct=((EXIT_PRICE_TARGET / opp["price"]) - 1) * 100 if opp["price"] > 0 else 0,
                                asset="BTC",
                                hours_left=opp.get("hours_left", 0),
                                no_price=opp["price"],
                                current_price=opp.get("current_price", 0),
                                target_price=EXIT_PRICE_TARGET,
                            )
                        except Exception as e:
                            logger.debug(f"Telegram notify error: {e}")
                        break  # Only one bot per opportunity
                    else:
                        reason = result.get("reason") or result.get("error", "unknown")
                        logger.warning(f"[{bot.name}] trade rejected: {reason}")

                # If no bot took this opportunity, mark the reason on the market
                if not taken and first_skip_reason:
                    market_tracker.mark_skipped(opp["id"], first_skip_reason)
                    logger.info(
                        f"⏸️  Skipped {opp['side'].upper()} @ {opp['price']:.3f} on "
                        f"\"{opp['question'][:50]}\" — reason: {first_skip_reason}"
                    )

            # Always check open positions after every scan — this is what
            # actually monitors the 30¢ target second-by-second.
            exit_monitor.check_all_positions()

            # Detailed periodic logging + Telegram summaries
            tick_counter[0] += 1
            now = time.time()

            # Every ~30s, log a one-line status to stdout
            if now - tick_counter[1] >= 30:
                tick_counter[1] = now
                _log_periodic_status()

            # Per-market closed summaries (always sent to Telegram)
            try:
                for closed_m in market_tracker.get_unsent_closed():
                    _send_market_summary(closed_m)
                    market_tracker.mark_summary_sent(closed_m["id"])
            except Exception as e:
                logger.debug(f"market summary send error: {e}")

            # Hourly digest
            if now - market_tracker.last_digest_ts >= 3600:
                market_tracker.last_digest_ts = now
                _send_hourly_digest()

            time.sleep(PRICE_POLL_INTERVAL_SEC)

        except KeyboardInterrupt:
            logger.info("Trading loop stopped")
            break
        except Exception as e:
            logger.error(f"Trading loop error: {e}", exc_info=True)
            time.sleep(5)


# ─── LEARNING LOOP (kept) ────────────────────────────────────
def outcome_learning_loop():
    logger.info("🧠 Learning loop started")
    while True:
        try:
            conn = db.get_conn()
            c = conn.cursor()
            c.execute("""
                SELECT * FROM trades
                WHERE outcome IN ('win', 'loss')
                AND resolved_at >= datetime('now', '-10 minutes')
            """)
            resolved = [dict(r) for r in c.fetchall()]
            conn.close()
            for trade in resolved:
                # Already notified in exit_monitor — this is just a placeholder
                # for any future learning hook.
                pass
            time.sleep(60)
        except Exception as e:
            logger.error(f"Learning loop error: {e}")
            time.sleep(30)


# ─── DAILY BACKUP (kept) ─────────────────────────────────────
def daily_backup_loop():
    while True:
        now = time.gmtime()
        if now.tm_hour == 23 and now.tm_min == 55:
            try:
                overview = db.get_overview()
                today = overview.get("today", {})
                notifier.daily_summary(
                    trades=today.get("total", 0),
                    wins=today.get("wins", 0),
                    pnl=today.get("pnl", 0),
                )
            except Exception:
                pass
            backup_to_github()
        time.sleep(60)


# ─── DASHBOARD (kept) ────────────────────────────────────────
def start_dashboard(bots):
    set_refs(bots, executor, exit_monitor, circuit_breaker, None, scanner)
    uvicorn.run(
        app,
        host=config.DASHBOARD_HOST,
        port=config.DASHBOARD_PORT,
        log_level="warning",
    )


# ─── MAIN ────────────────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("⚡ POLYMARKET BTC 5-MIN BOT STARTING")
    logger.info(f"   Mode: {config.TRADING_MODE.upper()}")
    logger.info(f"   Strategy: Buy side ≤ {ENTRY_PRICE_MAX:.2f}, sell at ≥ {EXIT_PRICE_TARGET:.2f}")
    logger.info(f"   Entry window: 0 → {ENTRY_WINDOW_END_SEC}s ({(300-ENTRY_WINDOW_END_SEC)//60}:{(300-ENTRY_WINDOW_END_SEC)%60:02d} remaining)")
    logger.info(f"   Force close:  t = {FORCE_CLOSE_SEC}s ({(300-FORCE_CLOSE_SEC)//60}:{(300-FORCE_CLOSE_SEC)%60:02d} remaining)")
    logger.info(f"   Trade size: ${PAPER_TRADE_SIZE_USD:.2f}")
    logger.info(f"   Poll interval: {PRICE_POLL_INTERVAL_SEC}s")
    logger.info(f"   Concurrency: 1 position globally")
    logger.info("=" * 60)

    restore_from_github()

    bots = create_bots()
    logger.info(f"✅ {len(bots)} bots initialized:")
    for bot in bots:
        remaining_at_deadline = 300 - bot.entry_window_end_sec
        logger.info(
            f"   {bot.name}: prefers={bot.side_preference} size=${bot.get_amount():.2f} "
            f"entry≤{bot.entry_window_end_sec}s ({remaining_at_deadline//60}:{remaining_at_deadline%60:02d} rem)"
        )

    # Background threads — reuse existing infrastructure
    threading.Thread(target=monitor_btc_crash, args=(bots,), daemon=True).start()
    threading.Thread(target=start_dashboard, args=(bots,), daemon=True).start()
    threading.Thread(target=outcome_learning_loop, daemon=True).start()
    threading.Thread(target=daily_backup_loop, daemon=True).start()

    logger.info(f"✅ Dashboard on port {config.DASHBOARD_PORT}")

    # Restore state
    restore_traded_market_ids(bots)
    notifier.bot_started(config.TRADING_MODE, len(bots))
    send_existing_trades_summary()

    # Main trading loop (blocking)
    trading_loop(bots)


if __name__ == "__main__":
    main()
