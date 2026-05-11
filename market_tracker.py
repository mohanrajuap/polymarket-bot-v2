"""
Market Tracker
Records the full lifecycle of every BTC 5-min market the bot observes:
  - When did the market open?
  - What was the cheapest side ever offered?
  - Did the bot enter? If not, why?
  - What was the final outcome?

Used for detailed logs and per-market Telegram summaries.
"""

import logging
import time
from datetime import datetime, timezone
from collections import OrderedDict
from threading import Lock

logger = logging.getLogger(__name__)


class MarketTracker:
    """Tracks each market we've observed during the run."""

    # Reasons a market was not entered
    REASON_PAST_ENTRY_DEADLINE = "past_entry_deadline"
    REASON_NO_CHEAP_SIDE = "no_side_under_20c"
    REASON_ALREADY_TRADED = "already_traded_this_market"
    REASON_GLOBAL_LOCK = "another_position_open"
    REASON_TRADED = "traded"
    REASON_UNKNOWN = "unknown"

    def __init__(self, max_markets: int = 500):
        self._markets: OrderedDict[str, dict] = OrderedDict()
        self._max = max_markets
        self._lock = Lock()
        # When was the last hourly digest sent?
        self.last_digest_ts = 0.0

    # ── INGEST ────────────────────────────────────────────────
    def observe(self, market_id: str, question: str, yes_price: float,
                no_price: float, elapsed_sec: int):
        """Called every scan tick for every active BTC market."""
        with self._lock:
            m = self._markets.get(market_id)
            if not m:
                m = {
                    "id": market_id,
                    "question": question,
                    "first_seen_ts": time.time(),
                    "first_seen_elapsed": elapsed_sec,
                    "min_yes_price": yes_price,
                    "min_no_price": no_price,
                    "min_yes_at_elapsed": elapsed_sec,
                    "min_no_at_elapsed": elapsed_sec,
                    "max_yes_price": yes_price,
                    "max_no_price": no_price,
                    "last_yes_price": yes_price,
                    "last_no_price": no_price,
                    "last_elapsed": elapsed_sec,
                    "tick_count": 1,
                    "entered": False,
                    "entry_side": None,
                    "entry_price": None,
                    "entry_elapsed": None,
                    "exit_price": None,
                    "exit_elapsed": None,
                    "exit_trigger": None,
                    "exit_outcome": None,
                    "exit_pnl": None,
                    "skip_reason": None,
                    "closed": False,
                    "summary_sent": False,
                }
                self._markets[market_id] = m
                # Bound memory
                if len(self._markets) > self._max:
                    self._markets.popitem(last=False)
            else:
                m["tick_count"] += 1
                if yes_price < m["min_yes_price"]:
                    m["min_yes_price"] = yes_price
                    m["min_yes_at_elapsed"] = elapsed_sec
                if no_price < m["min_no_price"]:
                    m["min_no_price"] = no_price
                    m["min_no_at_elapsed"] = elapsed_sec
                if yes_price > m["max_yes_price"]:
                    m["max_yes_price"] = yes_price
                if no_price > m["max_no_price"]:
                    m["max_no_price"] = no_price
                m["last_yes_price"] = yes_price
                m["last_no_price"] = no_price
                m["last_elapsed"] = elapsed_sec

    def mark_skipped(self, market_id: str, reason: str):
        """Record why we did not enter this market this tick.

        Only sticky reasons (past deadline, no cheap side seen, already traded,
        global lock) overwrite an existing reason. Lighter reasons are kept
        for diagnostic output but won't lie about a market we actually entered.
        """
        with self._lock:
            m = self._markets.get(market_id)
            if not m:
                return
            if m["entered"]:
                return
            # Once we record "no_cheap_side" we don't want to overwrite with
            # "global_lock" if the bot is busy elsewhere — keep whichever
            # reason was actually fatal.
            if not m.get("skip_reason"):
                m["skip_reason"] = reason

    def mark_entered(self, market_id: str, side: str, price: float,
                     elapsed: int):
        with self._lock:
            m = self._markets.get(market_id)
            if not m:
                return
            m["entered"] = True
            m["entry_side"] = side
            m["entry_price"] = price
            m["entry_elapsed"] = elapsed
            m["skip_reason"] = None  # clear any earlier skip reason

    def mark_exited(self, market_id: str, exit_price: float, elapsed: int,
                    trigger: str, outcome: str, pnl: float):
        with self._lock:
            m = self._markets.get(market_id)
            if not m:
                return
            m["exit_price"] = exit_price
            m["exit_elapsed"] = elapsed
            m["exit_trigger"] = trigger
            m["exit_outcome"] = outcome
            m["exit_pnl"] = pnl

    def mark_closed(self, market_id: str):
        """Called when a market reaches expiry (regardless of whether we traded)."""
        with self._lock:
            m = self._markets.get(market_id)
            if not m:
                return
            m["closed"] = True

    # ── QUERY ─────────────────────────────────────────────────
    def get(self, market_id: str) -> dict | None:
        with self._lock:
            return dict(self._markets[market_id]) if market_id in self._markets else None

    def get_unsent_closed(self) -> list[dict]:
        """Markets that have closed but we haven't sent a summary for yet."""
        with self._lock:
            out = []
            for m in self._markets.values():
                if m["closed"] and not m["summary_sent"]:
                    out.append(dict(m))
            return out

    def mark_summary_sent(self, market_id: str):
        with self._lock:
            if market_id in self._markets:
                self._markets[market_id]["summary_sent"] = True

    def recent(self, n: int = 12) -> list[dict]:
        """Return the most recently observed N markets."""
        with self._lock:
            return [dict(m) for m in list(self._markets.values())[-n:]]

    def stats_since(self, hours: float = 1.0) -> dict:
        """Aggregate stats across markets observed in the last N hours."""
        cutoff = time.time() - hours * 3600
        with self._lock:
            recent = [m for m in self._markets.values() if m["first_seen_ts"] >= cutoff]

        if not recent:
            return {"markets": 0}

        total = len(recent)
        traded = sum(1 for m in recent if m["entered"])
        wins = sum(1 for m in recent if m.get("exit_outcome") == "win")
        losses = sum(1 for m in recent if m.get("exit_outcome") == "loss")
        pnl = sum(m.get("exit_pnl") or 0.0 for m in recent)

        # Reasons for skipping
        reason_counts: dict[str, int] = {}
        for m in recent:
            if not m["entered"] and m.get("skip_reason"):
                r = m["skip_reason"]
                reason_counts[r] = reason_counts.get(r, 0) + 1

        # How many markets had a side reach ≤20¢ at any point
        cheap_side_seen = sum(
            1 for m in recent
            if min(m["min_yes_price"], m["min_no_price"]) <= 0.20
        )

        return {
            "markets": total,
            "traded": traded,
            "wins": wins,
            "losses": losses,
            "pnl": round(pnl, 2),
            "cheap_side_seen": cheap_side_seen,
            "skip_reasons": reason_counts,
        }


# Singleton
market_tracker = MarketTracker()
