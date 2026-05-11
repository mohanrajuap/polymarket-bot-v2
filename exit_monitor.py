"""
Exit Monitor for BTC 5-Minute Strategy

Polls every second. There is NO hold-to-expiry — every position MUST close
by t = FORCE_CLOSE_SEC (210s = 1:30 remaining).

Exit rules (in priority order):
  1. PROFIT: bought-side price >= EXIT_PRICE_TARGET (30¢) and elapsed < FORCE_CLOSE_SEC
     → sell at the current price for a profit
  2. STOP_LOSS (optional): current price <= EXIT_STOP_LOSS, only if EXIT_STOP_LOSS > 0
  3. FORCE_CLOSE: elapsed >= FORCE_CLOSE_SEC (210s)
     → close at current market price, win or loss. No exceptions.
  4. EXPIRY (safety net): elapsed >= WINDOW_DURATION_SEC (300s)
     → only fires if FORCE_CLOSE somehow missed; resolves by winner
"""

import logging
import time
import json
from datetime import datetime, timezone

import requests

import db
import config
from constants import (
    EXIT_PRICE_TARGET,
    EXIT_STOP_LOSS,
    EXIT_WINDOW_END_SEC,
    FORCE_CLOSE_SEC,
    WINDOW_DURATION_SEC,
    POLYMARKET_GAMMA_API,
    POLYMARKET_CLOB_API,
)
from execution.polymarket_client import polymarket_client
from execution.reconciler import reconciler
from risk.circuit_breaker import circuit_breaker
from brain import brain

logger = logging.getLogger(__name__)


def _clob_display_price(token_id: str) -> float | None:
    """Match Polymarket's UI display logic:
       - spread <= 0.10 → midpoint
       - spread >  0.10 → last trade price
    """
    def _get_float(url: str, params: dict, key: str) -> float | None:
        try:
            r = requests.get(url, params=params, timeout=5)
            if r.status_code != 200:
                return None
            v = r.json().get(key)
            return float(v) if v is not None else None
        except Exception:
            return None

    mid = _get_float(f"{POLYMARKET_CLOB_API}/midpoint", {"token_id": token_id}, "mid")
    spread = _get_float(f"{POLYMARKET_CLOB_API}/spread", {"token_id": token_id}, "spread")

    if mid is not None and spread is not None and spread <= 0.10:
        return mid
    last = _get_float(f"{POLYMARKET_CLOB_API}/last-trade-price",
                      {"token_id": token_id}, "price")
    if last is not None:
        return last
    return mid


def _get_side_price(market_id: str, side: str, token_id: str | None = None) -> float | None:
    """
    Return the live "display" price for the given side, matching Polymarket's UI.

    Strategy:
      1. If we have token_id → use CLOB display-price (midpoint or last trade)
      2. Otherwise look up market to find token, then CLOB
      3. Fall back to Gamma's cached outcomePrices
    """
    # ── Direct token-id path (fastest, what the UI shows) ────
    if token_id:
        p = _clob_display_price(token_id)
        if p is not None:
            return p

    # ── Look up the market and try CLOB via its tokens ───────
    try:
        r = requests.get(
            f"{POLYMARKET_GAMMA_API}/markets/{market_id}",
            timeout=5,
        )
        if r.status_code != 200:
            return None
        m = r.json()
        # Try CLOB display price first
        raw_tokens = m.get("clobTokenIds")
        if raw_tokens:
            try:
                tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
                tok = tokens[0] if side == "yes" else tokens[1] if len(tokens) > 1 else None
                if tok:
                    p = _clob_display_price(tok)
                    if p is not None:
                        return p
            except Exception:
                pass

        # ── Last resort: Gamma's possibly-stale snapshot ────
        raw = m.get("outcomePrices")
        if not raw:
            return None
        prices = json.loads(raw) if isinstance(raw, str) else raw
        yes = float(prices[0])
        no = float(prices[1]) if len(prices) > 1 else (1.0 - yes)
        return yes if side == "yes" else no
    except Exception as e:
        logger.debug(f"Price fetch error for {market_id}: {e}")
        return None


def _seconds_since_open(market_id: str) -> int | None:
    """Lookup market window-open time and return seconds elapsed.

    Uses the same multi-source resolution as the scanner:
      1. Slug-derived (most reliable for 5-min markets)
      2. endDate - WINDOW_DURATION_SEC
      3. startDate (last resort)
    """
    import re as _re
    try:
        r = requests.get(
            f"{POLYMARKET_GAMMA_API}/markets/{market_id}",
            timeout=5,
        )
        if r.status_code != 200:
            return None
        m = r.json()

        start_dt = None

        # 1. Slug pattern "btc-updown-5m-<unix_ts>"
        slug = (m.get("slug") or "").lower()
        slug_match = _re.search(r"-(\d{10})$", slug)
        if slug_match:
            try:
                close_ts = int(slug_match.group(1))
                start_dt = datetime.fromtimestamp(
                    close_ts - WINDOW_DURATION_SEC, tz=timezone.utc
                )
            except Exception:
                start_dt = None

        # 2. endDate - 5 minutes
        if start_dt is None:
            end = m.get("endDate") or m.get("end_date")
            if end:
                try:
                    end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                    from datetime import timedelta as _td
                    start_dt = end_dt - _td(seconds=WINDOW_DURATION_SEC)
                except Exception:
                    pass

        # 3. startDate (last resort)
        if start_dt is None:
            start = m.get("startDate") or m.get("start_date")
            if start:
                try:
                    start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                except Exception:
                    return None
            else:
                return None

        elapsed = int((datetime.now(timezone.utc) - start_dt).total_seconds())
        # Sanity check
        if elapsed < -60 or elapsed > 24 * 3600:
            return None
        return elapsed
    except Exception:
        return None


class ExitMonitor:
    """
    Monitors open BTC 5-min positions every second.

    For paper mode: when we hit 30¢ inside the window we *resolve the trade now*
    and book the profit, since paper trades aren't sitting on-chain.

    For live mode: we'd send a sell order to the CLOB. The skeleton is here;
    you'll need to wire up CLOB order placement before going live.
    """

    def __init__(self):
        self.exits_executed = 0
        self._running = False

    # ── BATCH CHECK (called from main loop) ──────────────────
    def check_all_positions(self):
        positions = db.get_open_positions()
        if not positions:
            return

        for pos in positions:
            try:
                self._check_position(pos)
            except Exception as e:
                logger.error(f"Exit monitor error for position {pos.get('id')}: {e}")

        # Let reconciler clean up anything Polymarket has resolved on-chain
        reconciler.reconcile_all()
        # Markets are 5 minutes; expire anything older than 10 minutes that's
        # still flagged open (most likely a missed reconcile).
        reconciler.expire_stale_trades(hours=0.2)

    # ── SINGLE POSITION ──────────────────────────────────────
    def _check_position(self, pos: dict):
        market_id = pos["market_id"]
        side = pos["side"]
        entry_price = pos["entry_price"]
        token_id = pos.get("token_id")  # may be missing on older trades

        elapsed = _seconds_since_open(market_id)
        current_price = _get_side_price(market_id, side, token_id=token_id)

        if current_price is not None:
            # Persist the latest price for the dashboard
            try:
                db.update_position_price(pos["trade_id"], current_price)
            except Exception:
                pass

        # Trigger 1: profit target hit BEFORE force-close deadline
        if (current_price is not None
                and current_price >= EXIT_PRICE_TARGET
                and elapsed is not None
                and elapsed < FORCE_CLOSE_SEC):
            self._exit_position(
                pos,
                exit_price=current_price,
                trigger="TARGET_HIT",
                reason=f"{side.upper()} hit {current_price:.3f} (target {EXIT_PRICE_TARGET:.2f}) at t={elapsed}s",
            )
            return

        # Trigger 2: optional stop loss
        if (EXIT_STOP_LOSS > 0
                and current_price is not None
                and current_price <= EXIT_STOP_LOSS):
            self._exit_position(
                pos,
                exit_price=current_price,
                trigger="STOP_LOSS",
                reason=f"{side.upper()} dropped to {current_price:.3f} (stop {EXIT_STOP_LOSS:.2f})",
            )
            return

        # Trigger 3: FORCE CLOSE — at t = FORCE_CLOSE_SEC, exit no matter what
        # (1:30 remaining). Win or loss, position closes here.
        if elapsed is not None and elapsed >= FORCE_CLOSE_SEC:
            # Use the live mid-price; if we couldn't fetch it, fall back to
            # the entry price so the trade resolves cleanly as a wash.
            close_price = current_price if current_price is not None else entry_price
            outcome_str = "WIN" if close_price > entry_price else "LOSS"
            self._exit_position(
                pos,
                exit_price=close_price,
                trigger="FORCE_CLOSE",
                reason=(
                    f"{side.upper()} force-closed at {close_price:.3f} "
                    f"(t={elapsed}s, deadline={FORCE_CLOSE_SEC}s) — {outcome_str}"
                ),
            )
            return

        # Trigger 4 (safety net): if FORCE_CLOSE somehow missed and the market
        # actually expired on Polymarket, resolve by winner.
        if elapsed is not None and elapsed >= WINDOW_DURATION_SEC:
            resolution = polymarket_client.check_resolution(market_id)
            if resolution and resolution.get("resolved"):
                winning_side = resolution.get("winning_side", "")
                won = (winning_side == side)
                exit_price = 1.0 if won else 0.0
                self._exit_position(
                    pos,
                    exit_price=exit_price,
                    trigger="EXPIRY",
                    reason=f"Safety-net resolve — winner: {winning_side.upper()}",
                )
            return

        # Otherwise: still inside the market lifetime, no trigger fired yet.
        if current_price is not None and elapsed is not None:
            logger.debug(
                f"[{market_id[:10]}] t={elapsed}s side={side.upper()} "
                f"price={current_price:.3f} (target {EXIT_PRICE_TARGET:.2f})"
            )

    # ── EXIT ─────────────────────────────────────────────────
    def _exit_position(self, pos: dict, exit_price: float, trigger: str, reason: str):
        trade_id = pos["trade_id"]
        amount = pos["amount"]
        entry_price = pos["entry_price"]
        shares = pos.get("shares", 0) or (amount / entry_price if entry_price > 0 else 0)
        side = pos["side"]

        # P&L calculation: for binary outcome shares, payout = shares * exit_price
        # because each share pays out at its current 0..1 price when sold.
        proceeds = shares * exit_price
        pnl = proceeds - amount
        outcome = "win" if pnl > 0 else "loss"

        # Live mode: actually send the sell order before booking
        if config.is_live() and trigger in ("TARGET_HIT", "STOP_LOSS"):
            try:
                # TODO: this is where you'd send a CLOB sell. For now we just log.
                logger.warning(
                    f"⚠️ LIVE SELL not yet implemented — trade {trade_id} would sell "
                    f"{shares:.2f} shares of {side.upper()} at {exit_price:.3f}"
                )
            except Exception as e:
                logger.error(f"Live sell error: {e}")

        # Update DB
        db.resolve_trade(
            trade_id=trade_id,
            outcome=outcome,
            pnl=round(pnl, 4),
            exit_price=exit_price,
        )
        db.remove_open_position(trade_id)

        if outcome == "win":
            circuit_breaker.record_win()
        else:
            circuit_breaker.record_loss()

        # Record on the market tracker
        try:
            from market_tracker import market_tracker
            market_id = pos.get("market_id", "")
            if market_id:
                # Approximate elapsed if we still have it
                elapsed_now = _seconds_since_open(market_id) or 0
                market_tracker.mark_exited(
                    market_id=market_id,
                    exit_price=exit_price,
                    elapsed=elapsed_now,
                    trigger=trigger,
                    outcome=outcome,
                    pnl=pnl,
                )
                # Force-close and target-hit close the trade for our purposes;
                # the actual market may continue running on Polymarket but we
                # don't care — we exited.
                market_tracker.mark_closed(market_id)
        except Exception as e:
            logger.debug(f"market_tracker exit record error: {e}")

        try:
            brain.record_outcome(
                market_question=pos.get("market_question", ""),
                conditions={"market_price": entry_price, "side": side},
                reasoning=f"Exit: {trigger}",
                outcome=outcome,
                pnl=pnl,
            )
        except Exception:
            pass

        self.exits_executed += 1
        icon = "✅" if outcome == "win" else "❌"
        logger.info(
            f"{icon} EXIT [{trigger}] {side.upper()} entry={entry_price:.3f} "
            f"exit={exit_price:.3f} P&L=${pnl:+.2f} — {reason}"
        )

        # Notify Telegram (uses existing notifier API)
        try:
            from notifier import notifier
            notifier.trade_resolved(
                outcome=outcome,
                pnl=pnl,
                market=pos.get("market_question", "")[:60],
                bot_name=pos.get("bot_name", ""),
                amount=amount,
                trigger=trigger,
            )
        except Exception:
            pass

    # ── STATUS ───────────────────────────────────────────────
    def get_status(self) -> dict:
        positions = db.get_open_positions()
        return {
            "open_positions": len(positions),
            "exits_executed": self.exits_executed,
        }


exit_monitor = ExitMonitor()
