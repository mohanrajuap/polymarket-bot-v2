"""
BTC 5-Minute Market Scanner

Replaces the old "impossible bond" scanner.

Finds active Bitcoin Up/Down 5-minute markets where:
  1. The market is currently inside the entry window
     (0 to 90 seconds since market opened, i.e. 5:00 → 3:30 remaining)
  2. At least one side (YES or NO) is priced at or below ENTRY_PRICE_MAX (20¢)

Each opportunity returned contains everything the trading loop needs
to fire the executor, including the cheap side, its current price,
and the exact second-mark inside the window.
"""

import requests
import json
import logging
import time
import re
from datetime import datetime, timezone, timedelta

from constants import (
    POLYMARKET_GAMMA_API,
    POLYMARKET_CLOB_API,
    ENTRY_WINDOW_START_SEC,
    ENTRY_WINDOW_END_SEC,
    FORCE_CLOSE_SEC,
    WINDOW_DURATION_SEC,
    ENTRY_PRICE_MAX,
    MARKET_DISCOVERY_INTERVAL_SEC,
    MIN_LIQUIDITY,
)

logger = logging.getLogger(__name__)


class BTC5MinScanner:
    """Scans Polymarket for tradable BTC 5-min markets."""

    def __init__(self):
        self._market_cache = []
        self._last_market_fetch = 0
        self._prices = {"BTC": 0.0}
        self._last_price_fetch = 0
        self._radar = []  # last batch of opportunities (for the dashboard)
        # Reuse one Session for CLOB price fetches — keep-alive saves ~50ms/req
        self._clob_session = requests.Session()
        self._clob_session.headers.update({"User-Agent": "polymarket-bot/1.0"})

    # ── BTC SPOT (used by main's crash monitor) ──────────────
    def fetch_all_prices(self):
        """Maintain compatibility with main.py's BTC crash monitor."""
        if time.time() - self._last_price_fetch < 30:
            return self._prices

        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
                timeout=5,
            )
            self._prices["BTC"] = float(r.json()["price"])
            self._last_price_fetch = time.time()
        except Exception as e:
            logger.error(f"BTC spot fetch error: {e}")

        return self._prices

    # ── MARKET DISCOVERY ──────────────────────────────────────
    def _is_btc_5min_market(self, market: dict) -> bool:
        """Return True if this market is a BTC 5-minute Up/Down market."""
        question = (market.get("question") or "").lower()
        slug = (market.get("slug") or "").lower()

        # PREFERRED: detect by slug pattern (deterministic, lowercase)
        # Polymarket uses slugs like "btc-updown-5m-1715361600" for BTC 5m markets
        if slug.startswith("btc-updown-5m-"):
            return True
        if slug.startswith("bitcoin-updown-5m-"):  # alternative form, just in case
            return True

        # FALLBACK: detect by question text. Older / unusual markets may not match
        # the slug pattern but still describe a BTC 5-min Up/Down market.
        if "bitcoin" not in question and "btc" not in question:
            return False
        if "up or down" not in question and "up/down" not in question:
            return False
        # Reject hourly / daily windows: a 5-min market mentions two HH:MM times
        time_pattern = re.compile(r"\d{1,2}:\d{2}\s*(?:am|pm)", re.I)
        times = time_pattern.findall(question)
        if len(times) < 2:
            return False
        return True

    def _slug_window_open_ts(self, market: dict) -> int | None:
        """
        Polymarket 5-min markets use deterministic slugs that *contain* the
        Unix timestamp at which the window CLOSES (always divisible by 300).

        e.g. slug = "btc-updown-5m-1715361600" → window closes at 1715361600,
        therefore window OPENED at 1715361600 - 300.

        Returns the unix-ts the window opened, or None if it can't be parsed.
        """
        slug = market.get("slug") or ""
        m = re.search(r"-(\d{10})$", slug)
        if not m:
            return None
        try:
            close_ts = int(m.group(1))
            return close_ts - WINDOW_DURATION_SEC
        except Exception:
            return None

    def _market_start_time(self, market: dict) -> datetime | None:
        """
        Returns the WINDOW open time (timezone-aware UTC), in priority order:
          1. Parse from the slug's unix timestamp     (most reliable)
          2. endDate - WINDOW_DURATION_SEC            (also reliable)
          3. startDate                                (last resort, often wrong)
        """
        # 1. Slug-derived (deterministic for 5-min markets)
        slug_ts = self._slug_window_open_ts(market)
        if slug_ts is not None:
            return datetime.fromtimestamp(slug_ts, tz=timezone.utc)

        # 2. endDate - 5 minutes
        end = market.get("endDate") or market.get("end_date")
        if end:
            try:
                end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                return end_dt - timedelta(seconds=WINDOW_DURATION_SEC)
            except Exception:
                pass

        # 3. startDate (legacy fallback — known to be unreliable)
        start = market.get("startDate") or market.get("start_date") or market.get("gameStartTime")
        if start:
            try:
                if isinstance(start, str):
                    return datetime.fromisoformat(start.replace("Z", "+00:00"))
            except Exception:
                pass

        return None

    def _seconds_since_open(self, market: dict) -> int | None:
        """How many seconds have elapsed since this market opened. None if unknown."""
        start = self._market_start_time(market)
        if not start:
            return None
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        # Sanity guard: if the elapsed time is wildly off (negative far-past or
        # very far in the future), reject the market — its timestamps are bogus.
        if elapsed < -60 or elapsed > 24 * 3600:
            return None
        return int(elapsed)

    def _in_entry_window(self, seconds_elapsed: int) -> bool:
        # Scanner surfaces every opportunity up to the force-close mark.
        # Individual bots apply their own (narrower) entry-window filter
        # in BTC5MinBot.can_trade(), so the late-entry bot can still trade.
        return ENTRY_WINDOW_START_SEC <= seconds_elapsed < FORCE_CLOSE_SEC

    def _get_clob_display_price(self, token_id: str) -> float | None:
        """Fetch the price that Polymarket's UI displays for this token.

        Polymarket's logic (from their docs):
          - If spread <= $0.10  → midpoint = (best_bid + best_ask) / 2
          - If spread >  $0.10  → last trade price

        Neither /price nor /midpoint alone is reliable for short-lived
        5-min markets:
          - /price?side=BUY returns the best ASK (often a sparse extreme value)
          - /midpoint can be wildly off when the spread is wide
          - The frontend uses the last trade price in wide-spread cases

        This implements the same logic so our prices match the UI.
        """
        if not token_id:
            return None

        # Step 1: fetch midpoint + spread in parallel-ish (one session)
        mid = self._clob_get_float(f"{POLYMARKET_CLOB_API}/midpoint",
                                   {"token_id": token_id}, "mid")
        spread = self._clob_get_float(f"{POLYMARKET_CLOB_API}/spread",
                                      {"token_id": token_id}, "spread")

        # Step 2: if spread is narrow (<=10¢), midpoint matches the UI
        if mid is not None and spread is not None and spread <= 0.10:
            return mid

        # Step 3: wide spread → UI shows last trade price
        last = self._clob_get_float(f"{POLYMARKET_CLOB_API}/last-trade-price",
                                    {"token_id": token_id}, "price")
        if last is not None:
            return last

        # Step 4: fall back to midpoint even if spread is wide (best we have)
        return mid

    def _clob_get_float(self, url: str, params: dict, key: str) -> float | None:
        """Helper: GET a CLOB endpoint, parse a single float field from JSON."""
        try:
            r = self._clob_session.get(url, params=params, timeout=5)
            if r.status_code != 200:
                return None
            data = r.json()
            v = data.get(key)
            return float(v) if v is not None else None
        except Exception as e:
            logger.debug(f"CLOB fetch error ({url}): {e}")
            return None

    def _get_outcome_prices(self, market: dict) -> tuple[float, float] | None:
        """
        Return live (yes_price, no_price) for a BTC 5-min market — matching
        what Polymarket's UI displays.

        Strategy:
          1. Use CLOB's display-price logic (midpoint or last trade) per token
          2. Fall back to Gamma's cached outcomePrices if CLOB fails

        Gamma's outcomePrices is often minutes stale on 5-min markets.
        """
        yes_token, no_token = self._get_token_ids(market)

        # Try live display prices via CLOB
        if yes_token and no_token:
            yes_clob = self._get_clob_display_price(yes_token)
            no_clob = self._get_clob_display_price(no_token)
            if yes_clob is not None and no_clob is not None:
                return yes_clob, no_clob

        # Fallback: Gamma cached prices (may be stale)
        raw = market.get("outcomePrices")
        if not raw:
            return None
        try:
            if isinstance(raw, str):
                prices = json.loads(raw)
            else:
                prices = raw
            yes = float(prices[0])
            no = float(prices[1]) if len(prices) > 1 else (1.0 - yes)
            return yes, no
        except Exception:
            return None

    def _get_token_ids(self, market: dict) -> tuple[str | None, str | None]:
        """Return (yes_token_id, no_token_id) for placing orders."""
        raw = market.get("clobTokenIds")
        if not raw:
            return None, None
        try:
            tokens = json.loads(raw) if isinstance(raw, str) else raw
            yes_id = tokens[0] if len(tokens) > 0 else None
            no_id = tokens[1] if len(tokens) > 1 else None
            return yes_id, no_id
        except Exception:
            return None, None

    def _current_window_unix_ts(self) -> tuple[int, int]:
        """
        Return (window_open_ts, window_close_ts) for the BTC 5-min market that
        is *currently* live, aligned to 5-minute boundaries.

        Example: if now = 21:42:15 UTC,
                 window_open = 21:40:00, window_close = 21:45:00
        """
        now_ts = int(time.time())
        window_open = (now_ts // WINDOW_DURATION_SEC) * WINDOW_DURATION_SEC
        window_close = window_open + WINDOW_DURATION_SEC
        return window_open, window_close

    def _candidate_slugs(self) -> list[str]:
        """
        Polymarket's BTC 5-min slug is: btc-updown-5m-<window_open_unix_ts>

        We try:
          1. The current window (most likely live)
          2. The previous window (boundary race — we just rolled over, but the
             previous market might still be resolving its final tick)
        """
        open_ts, _close = self._current_window_unix_ts()
        return [
            f"btc-updown-5m-{open_ts}",
            f"btc-updown-5m-{open_ts - WINDOW_DURATION_SEC}",
        ]

    def _fetch_by_slug(self, slug: str) -> dict | None:
        """Hit /markets?slug=<slug> directly. Return the market dict or None."""
        try:
            r = requests.get(
                f"{POLYMARKET_GAMMA_API}/markets",
                params={"slug": slug, "limit": 1},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict) and data.get("question"):
                return data
            return None
        except Exception as e:
            logger.debug(f"slug fetch error ({slug}): {e}")
            return None

    def _fetch_active_btc_markets(self) -> list[dict]:
        """
        Pull the active BTC 5-min market via deterministic slug lookup.

        We DON'T use /markets?active=true — the user confirmed via diagnostic
        that BTC 5-min markets do NOT appear in that listing (likely because
        they're so short-lived). The slug-direct lookup is fast (<300ms),
        deterministic, and works reliably.
        """
        if time.time() - self._last_market_fetch < MARKET_DISCOVERY_INTERVAL_SEC:
            return self._market_cache

        results: dict[str, dict] = {}

        for slug in self._candidate_slugs():
            m = self._fetch_by_slug(slug)
            if m and self._is_btc_5min_market(m):
                mid = str(m.get("id") or m.get("conditionId") or m.get("slug"))
                results[mid] = m
                logger.debug(f"[scanner] ✓ found market by slug: {slug}")

        btc_markets = list(results.values())
        if btc_markets:
            self._market_cache = btc_markets
            self._last_market_fetch = time.time()
        else:
            # Don't clobber a previously-good cache with an empty result —
            # keep serving the last known market for one more tick.
            self._last_market_fetch = time.time()

        return btc_markets

    # ── PUBLIC SCAN ──────────────────────────────────────────
    def scan(self) -> list[dict]:
        """
        Returns a list of opportunity dicts. Each opportunity has:
          {
            "id":            <gamma market id>,
            "question":      <market question>,
            "side":          "yes" | "no",      # the cheap side we'd buy
            "price":         <0..1 float>,       # current price of that side
            "price_yes":     <yes price>,
            "price_no":      <no price>,
            "token_id":      <CLOB token id for the cheap side>,
            "yes_token_id":  <CLOB token id for YES>,
            "no_token_id":   <CLOB token id for NO>,
            "seconds_elapsed": <int>,            # since market open
            "seconds_to_window_end": <int>,      # seconds left in entry window
          }
        """
        # Lazy import to avoid circulars at module load time
        from market_tracker import market_tracker

        opportunities = []
        markets = self._fetch_active_btc_markets()
        observed_count = 0
        in_window_count = 0
        cheap_count = 0

        for m in markets:
            # Defense-in-depth: re-check market type (cache may contain stale junk)
            if not self._is_btc_5min_market(m):
                continue

            elapsed = self._seconds_since_open(m)
            if elapsed is None:
                continue

            prices = self._get_outcome_prices(m)
            if not prices:
                continue
            yes_price, no_price = prices

            mid = str(m.get("id") or m.get("conditionId") or "")
            question = m.get("question", "")

            # Always observe — even outside window — so we can summarize later
            market_tracker.observe(mid, question, yes_price, no_price, elapsed)
            observed_count += 1

            # If the market has expired, mark it closed for summary purposes
            if elapsed >= WINDOW_DURATION_SEC:
                market_tracker.mark_closed(mid)
                continue

            # Filter 1: inside the entry window?
            if not self._in_entry_window(elapsed):
                market_tracker.mark_skipped(mid, market_tracker.REASON_PAST_ENTRY_DEADLINE)
                continue
            in_window_count += 1

            # Filter 2: at least one side under 20¢?
            cheap_side = None
            cheap_price = 1.0
            if yes_price <= ENTRY_PRICE_MAX and yes_price < cheap_price:
                cheap_side, cheap_price = "yes", yes_price
            if no_price <= ENTRY_PRICE_MAX and no_price < cheap_price:
                cheap_side, cheap_price = "no", no_price

            if cheap_side is None:
                market_tracker.mark_skipped(mid, market_tracker.REASON_NO_CHEAP_SIDE)
                continue
            cheap_count += 1

            yes_token_id, no_token_id = self._get_token_ids(m)
            token_id = yes_token_id if cheap_side == "yes" else no_token_id

            opp = {
                "id": mid,
                "question": question,
                "side": cheap_side,
                "price": cheap_price,
                "price_yes": yes_price,
                "price_no": no_price,
                "token_id": token_id,
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
                "seconds_elapsed": elapsed,
                "seconds_to_window_end": ENTRY_WINDOW_END_SEC - elapsed,
                "asset": "BTC",
                # legacy compatibility fields
                "win_probability": 0.5,
                "gap_pct": 0.0,
                "yield_pct": 0.0,
                "current_price": self._prices.get("BTC", 0.0),
                "target_price": 0.0,
                "hours_left": (WINDOW_DURATION_SEC - elapsed) / 3600.0,
            }
            opportunities.append(opp)

        # Sort by cheapest side first (best value)
        opportunities.sort(key=lambda o: o["price"])
        self._radar = opportunities

        # Detailed scan log every tick — quiet at debug, but key counts at info
        if observed_count > 0 or markets:
            logger.debug(
                f"[scan] markets={observed_count} in_entry_window={in_window_count} "
                f"with_cheap_side={cheap_count} opportunities={len(opportunities)}"
            )

        return opportunities

    def get_radar(self) -> list[dict]:
        """For the dashboard."""
        return self._radar


scanner = BTC5MinScanner()
