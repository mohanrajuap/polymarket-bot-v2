"""
Telegram Alerts - Complete Integration with Rich Formatting
"""

import requests
import logging
import time
import config

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot"


class Notifier:

    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        self._last_sent = 0
        self._rate_limit = 1

        if self.enabled:
            logger.info("✅ Telegram notifications enabled")
        else:
            logger.info("⚠️ Telegram disabled - set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    def send(self, message: str, silent: bool = False):
        if not self.enabled:
            return False

        now = time.time()
        if now - self._last_sent < self._rate_limit:
            time.sleep(self._rate_limit)

        try:
            url = f"{TELEGRAM_API}{self.token}/sendMessage"
            r = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_notification": silent,
            }, timeout=8)

            if r.status_code == 200:
                self._last_sent = time.time()
                return True
            else:
                logger.error(f"Telegram error: {r.status_code} {r.text[:100]}")
                return False

        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    # ── BOT STARTED ───────────────────────────────────────────
    def bot_started(self, mode: str, num_bots: int):
        self.send(
            f"⚡ <b>POLYMARKET BTC 5-MIN BOT STARTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Mode:     <code>{mode.upper()}</code>\n"
            f"🤖 Bots:     <code>{num_bots} active</code>\n"
            f"📊 Entry:    <code>≤20¢</code>\n"
            f"   • 3 bots: first 1:30 (0→90s)\n"
            f"   • 1 bot: full window (0→210s)\n"
            f"💰 Profit:   <code>sell at ≥30¢</code>\n"
            f"⏱ Force:    <code>close at 1:30 remaining</code>\n"
            f"📦 Limit:    <code>1 position globally</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Polling Polymarket every second."
        )

    # ── TRADE PLACED ─────────────────────────────────────────
    def trade_placed(self, bot_name: str, side: str, amount: float,
                     market: str, confidence: float,
                     gap_pct: float = 0, yield_pct: float = 0,
                     asset: str = "", hours_left: float = 0,
                     no_price: float = 0, current_price: float = 0,
                     target_price: float = 0):

        payout = round(amount / no_price, 2) if no_price > 0 else 0
        profit = round(payout - amount, 2)

        hours_str = f"{hours_left:.1f}h" if hours_left > 0 else "?"
        asset_emoji = {
            "BTC": "₿", "ETH": "Ξ", "SOL": "◎",
            "XRP": "✕", "DOGE": "🐕", "GOLD": "🥇",
            "OIL": "🛢", "SPX": "📈",
        }.get(asset, "📊")

        self.send(
            f"🟢 <b>NEW BOND TRADE PLACED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{asset_emoji} <b>Asset:</b>    {asset}\n"
            f"🤖 <b>Bot:</b>      <code>{bot_name}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>Market:</b>\n"
            f"<i>{market[:80]}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Trade Details:</b>\n"
            f"   Side:       <b>BUY NO</b>\n"
            f"   Amount:     <b>${amount:.2f}</b>\n"
            f"   NO Price:   <code>{no_price:.3f}</code>\n"
            f"   Payout:     <code>${payout:.2f}</code>\n"
            f"   Profit:     <code>+${profit:.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Analysis:</b>\n"
            f"   Gap needed: <code>{gap_pct:.1f}%</code> price move\n"
            f"   Win prob:   <code>{confidence*100:.0f}%</code>\n"
            f"   Yield:      <code>{yield_pct:.1f}%</code>\n"
            f"   Closes in:  <code>{hours_str}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Expecting to WIN this trade!"
        )

    # ── TRADE RESOLVED ────────────────────────────────────────
    def trade_resolved(self, outcome: str, pnl: float, market: str,
                       bot_name: str = "", amount: float = 0,
                       yield_pct: float = 0, trigger: str = ""):

        if outcome == "win":
            icon = "✅"
            header = "TRADE WON"
            pnl_str = f"+${pnl:.2f} 💰"
            msg_end = "Profit booked."
        else:
            icon = "❌"
            header = "TRADE LOST"
            pnl_str = f"-${abs(pnl):.2f}"
            msg_end = "Position closed at a loss."

        # Map exit trigger → human-readable reason
        trigger_label = {
            "TARGET_HIT":  "Profit target (≥30¢)",
            "FORCE_CLOSE": "Force-close at 1:30 remaining",
            "STOP_LOSS":   "Stop loss",
            "EXPIRY":      "Market expired",
        }.get(trigger, trigger or "—")

        bot_str = f"\n🤖 <b>Bot:</b>  <code>{bot_name}</code>" if bot_name else ""
        amt_str = f"\n💵 <b>Stake:</b> <code>${amount:.2f}</code>" if amount > 0 else ""
        trig_str = f"\n🎯 <b>Trigger:</b> <code>{trigger_label}</code>" if trigger else ""

        self.send(
            f"{icon} <b>{header}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>Market:</b>\n"
            f"<i>{market[:80]}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Result:</b>  <b>{pnl_str}</b>"
            f"{bot_str}"
            f"{amt_str}"
            f"{trig_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{msg_end}"
        )

    # ── SCAN COMPLETE ─────────────────────────────────────────
    def scan_complete(self, total_found: int, top_bonds: list):
        if not top_bonds:
            return  # silent if no bonds

        lines = [
            f"🎯 <b>BOND RADAR — {total_found} OPPORTUNITIES</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━",
        ]

        for i, b in enumerate(top_bonds[:5], 1):
            asset = b.get("asset", "")
            gap = b.get("gap_pct", 0)
            win = b.get("win_probability", 0) * 100
            yld = b.get("yield_pct", 0)
            hrs = b.get("hours_left", 0)
            no  = b.get("price_no", 0)
            q   = b.get("question", "")[:55]

            lines.append(
                f"\n{i}. <b>{asset}</b> | Gap: <code>{gap:.0f}%</code> | "
                f"Win: <code>{win:.0f}%</code> | Yield: <code>{yld:.1f}%</code>\n"
                f"   NO: <code>{no:.2f}</code> | Closes: <code>{hrs:.1f}h</code>\n"
                f"   <i>{q}</i>"
            )

        self.send("\n".join(lines), silent=True)

    # ── CIRCUIT BREAKER ───────────────────────────────────────
    def circuit_breaker_tripped(self, reason: str):
        self.send(
            f"🚨 <b>CIRCUIT BREAKER TRIPPED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Reason: <b>{reason}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⛔ <b>ALL TRADING STOPPED</b>\n"
            f"🔄 Auto-resets in 5 minutes"
        )

    def circuit_breaker_reset(self):
        self.send(
            f"🟢 <b>Circuit Breaker RESET</b>\n"
            f"Trading has resumed normally."
        )

    # ── BTC CRASH WARNING ─────────────────────────────────────
    def btc_crash_warning(self, drop_pct: float, current_price: float):
        if drop_pct >= 7:
            msg = (
                f"🛑 <b>BTC CRASH — EMERGENCY EXIT!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📉 Drop:   <b>{drop_pct:.1f}%</b>\n"
                f"💰 Price:  <b>${current_price:,.0f}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🛑 All bots STOPPED!\n"
                f"⚠️ Review open positions immediately!"
            )
        else:
            msg = (
                f"⚠️ <b>BTC CRASH WARNING</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📉 Drop:   <code>{drop_pct:.1f}%</code>\n"
                f"💰 Price:  <code>${current_price:,.0f}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👀 Monitoring closely...\n"
                f"🔴 Emergency exit at -7%"
            )
        self.send(msg)

    # ── DAILY SUMMARY ─────────────────────────────────────────
    def daily_summary(self, trades: int, wins: int, pnl: float,
                      open_positions: int = 0):
        losses = trades - wins
        wr = wins / trades * 100 if trades > 0 else 0
        pnl_icon = "📈" if pnl >= 0 else "📉"
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

        self.send(
            f"{pnl_icon} <b>DAILY SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Performance:</b>\n"
            f"   Trades:    <code>{trades}</code>\n"
            f"   Wins:      <code>{wins} ✅</code>\n"
            f"   Losses:    <code>{losses} ❌</code>\n"
            f"   Win Rate:  <b>{wr:.1f}%</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>P&L Today: <b>{pnl_str}</b></b>\n"
            f"📂 Open Positions: <code>{open_positions}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 Bot continues trading tomorrow!"
        )

    # ── DAILY LOSS LIMIT ─────────────────────────────────────
    def daily_loss_limit_hit(self, loss: float, limit: float):
        self.send(
            f"🛑 <b>DAILY LOSS LIMIT HIT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📉 Loss:  <code>${loss:.2f}</code>\n"
            f"🔴 Limit: <code>${limit:.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⛔ Trading PAUSED until tomorrow."
        )

    # ── PER-MARKET SUMMARY ───────────────────────────────────
    def market_summary(self, question: str, entered: bool,
                       entry_side=None, entry_price=None, entry_elapsed=None,
                       exit_price=None, exit_trigger=None,
                       exit_outcome=None, exit_pnl=None,
                       min_yes=None, min_no=None,
                       min_yes_at=None, min_no_at=None,
                       skip_reason=None, tick_count=0):
        """One-line summary per 5-min market when it closes.

        ALWAYS sends — entered or not — so you get full visibility.
        """
        # Header
        if entered:
            if exit_outcome == "win":
                icon, header = "✅", "MARKET CLOSED — TRADED (WIN)"
            elif exit_outcome == "loss":
                icon, header = "❌", "MARKET CLOSED — TRADED (LOSS)"
            else:
                icon, header = "📊", "MARKET CLOSED — TRADED"
        else:
            icon, header = "⏸️", "MARKET CLOSED — NO TRADE"

        # Cheapest side seen during the window
        min_yes_str = f"{min_yes:.3f}" if min_yes is not None else "?"
        min_no_str = f"{min_no:.3f}" if min_no is not None else "?"
        cheapest = None
        if min_yes is not None and min_no is not None:
            cheapest = min(min_yes, min_no)

        # Body
        body_lines = [
            f"{icon} <b>{header}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📋 <i>{question}</i>",
            "",
            f"📈 YES min: <code>{min_yes_str}</code> @ t={min_yes_at}s",
            f"📉 NO  min: <code>{min_no_str}</code> @ t={min_no_at}s",
            f"🔢 Ticks observed: <code>{tick_count}</code>",
        ]

        if entered:
            body_lines += [
                "",
                f"🎯 Bought: <b>{(entry_side or '').upper()}</b> @ <code>{entry_price:.3f}</code> (t={entry_elapsed}s)",
                f"🚪 Exit:  <b>{exit_trigger}</b> @ <code>{exit_price:.3f}</code>",
                f"💰 P&L:   <code>${exit_pnl:+.2f}</code>" if exit_pnl is not None else "💰 P&L:   pending",
            ]
        else:
            reason_label = {
                "past_entry_deadline":      "Past 1:30 entry deadline",
                "no_side_under_20c":        "No side reached ≤20¢",
                "already_traded_this_market": "Already traded this market",
                "another_position_open":    "Another position was open",
            }.get(skip_reason, skip_reason or "—")
            cheapest_str = f"{cheapest:.3f}" if cheapest is not None else "?"
            body_lines += [
                "",
                f"❓ Why no entry: <code>{reason_label}</code>",
                f"💡 Cheapest side: <code>{cheapest_str}</code> "
                f"({'✓ would qualify' if cheapest is not None and cheapest <= 0.20 else '✗ never ≤0.20'})",
            ]

        body_lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        self.send("\n".join(body_lines), silent=True)

    # ── HOURLY DIGEST ────────────────────────────────────────
    def hourly_digest(self, stats: dict):
        """One Telegram per hour with aggregate stats."""
        markets = stats.get("markets", 0)
        traded = stats.get("traded", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        pnl = stats.get("pnl", 0.0)
        cheap_seen = stats.get("cheap_side_seen", 0)
        skip_reasons = stats.get("skip_reasons", {})

        win_rate_pct = (100.0 * wins / max(1, traded))
        miss_rate_pct = 100.0 * (cheap_seen - traded) / max(1, cheap_seen) if cheap_seen else 0

        lines = [
            "📊 <b>HOURLY DIGEST</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🪟 Markets observed: <code>{markets}</code>",
            f"💎 Markets with side ≤20¢: <code>{cheap_seen}</code>",
            f"📈 Trades taken: <code>{traded}</code>",
            f"   ✅ Wins:   <code>{wins}</code>",
            f"   ❌ Losses: <code>{losses}</code>",
            f"💰 P&L this hour: <code>${pnl:+.2f}</code>",
        ]
        if traded > 0:
            lines.append(f"🎯 Win rate: <code>{win_rate_pct:.1f}%</code>")
        if cheap_seen > traded:
            lines.append(f"⚠️ Missed entries: <code>{cheap_seen - traded}</code> ({miss_rate_pct:.0f}%)")

        if skip_reasons:
            lines.append("")
            lines.append("🔍 Skip reasons:")
            for r, n in sorted(skip_reasons.items(), key=lambda x: -x[1]):
                pretty = {
                    "past_entry_deadline":        "past 1:30 deadline",
                    "no_side_under_20c":          "no side ≤20¢",
                    "already_traded_this_market": "already traded",
                    "another_position_open":      "global lock",
                }.get(r, r)
                lines.append(f"   • {pretty}: <code>{n}</code>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        self.send("\n".join(lines), silent=True)

    # ── TEST ─────────────────────────────────────────────────
    def test(self):
        return self.send(
            f"🧪 <b>POLYMARKET BOND BOT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Telegram connected!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📲 <b>You will receive:</b>\n"
            f"   🟢 Every trade placed\n"
            f"   ✅ Every trade won\n"
            f"   ❌ Every trade lost\n"
            f"   🎯 Market radar (silent)\n"
            f"   🚨 Circuit breaker alerts\n"
            f"   ⚠️ BTC crash warnings\n"
            f"   📊 Daily summary\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Bot is running! 🚀"
        )


notifier = Notifier()
