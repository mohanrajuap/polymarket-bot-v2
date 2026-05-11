"""
Polymarket CLOB client - direct trade execution.
Supports both PAPER (simulated) and LIVE modes.
"""

import requests
import json
import logging
import time
from datetime import datetime, timezone
import config
from constants import POLYMARKET_CLOB_API, POLYMARKET_GAMMA_API

logger = logging.getLogger(__name__)


class PolymarketClient:
    """
    Direct Polymarket API client.
    Paper mode: simulates trades locally.
    Live mode: executes real trades on Polygon chain.
    """

    def __init__(self):
        self.mode = config.TRADING_MODE
        self._paper_balance = config.PAPER_STARTING_BALANCE
        self._paper_trades = {}
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "PolymarketBot/1.0"
        })

    def place_order(self, market_id: str, side: str, amount: float,
                    market_question: str = "") -> dict:
        """
        Place a trade order.
        Paper mode: simulate locally.
        Live mode: execute on Polymarket CLOB.
        """
        if config.is_paper():
            return self._paper_order(market_id, side, amount, market_question)
        else:
            return self._live_order(market_id, side, amount)

    def _paper_order(self, market_id: str, side: str, amount: float,
                     market_question: str) -> dict:
        """Simulate trade in paper mode"""
        if amount > self._paper_balance:
            return {"success": False, "error": "insufficient_balance"}

        # Use the price of the SIDE we're buying, not just YES
        price = self._get_side_price(market_id, side)
        if not price:
            price = 0.51  # fallback near 50/50

        shares = amount / price if price > 0 else 0

        trade_id = f"paper_{market_id}_{int(time.time())}"
        self._paper_trades[trade_id] = {
            "market_id": market_id,
            "side": side,
            "amount": amount,
            "price": price,
            "shares": shares,
            "status": "open",
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }

        self._paper_balance -= amount

        logger.info(
            f"📄 PAPER TRADE: {side.upper()} ${amount:.2f} "
            f"@ {price:.3f} on {market_question[:40]}"
        )

        return {
            "success": True,
            "trade_id": trade_id,
            "price": price,
            "shares": shares,
            "amount": amount,
            "mode": "paper",
        }

    def _get_side_price(self, market_id: str, side: str) -> float | None:
        """Return the live price of YES or NO for this market."""
        try:
            r = self._session.get(
                f"{POLYMARKET_GAMMA_API}/markets/{market_id}",
                timeout=5,
            )
            if r.status_code != 200:
                return None
            m = r.json()
            raw = m.get("outcomePrices", "[0.5,0.5]")
            prices = json.loads(raw) if isinstance(raw, str) else raw
            yes = float(prices[0])
            no = float(prices[1]) if len(prices) > 1 else (1.0 - yes)
            return yes if side.lower() == "yes" else no
        except Exception:
            return None

    def _live_order(self, market_id: str, side: str, amount: float) -> dict:
        """Execute real order on Polymarket CLOB"""
        if not config.POLYMARKET_PRIVATE_KEY:
            return {"success": False, "error": "no_private_key"}

        try:
            # Get market token IDs
            market = self._get_market(market_id)
            if not market:
                return {"success": False, "error": "market_not_found"}

            tokens = market.get("tokens", [])
            token_id = None
            for t in tokens:
                outcome = t.get("outcome", "").lower()
                if (side == "yes" and outcome == "yes") or (side == "no" and outcome == "no"):
                    token_id = t.get("token_id")
                    break

            if not token_id:
                return {"success": False, "error": "token_not_found"}

            # Place market order via CLOB
            resp = self._session.post(
                f"{POLYMARKET_CLOB_API}/order",
                json={
                    "tokenID": token_id,
                    "price": 0.5,  # market order
                    "side": "BUY",
                    "size": amount,
                    "orderType": "FOK",  # Fill or Kill
                },
                timeout=10
            )

            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "trade_id": data.get("orderID"),
                    "price": data.get("price", 0),
                    "shares": data.get("sizeFilled", 0),
                    "amount": amount,
                    "mode": "live",
                }
            else:
                return {"success": False, "error": f"api_{resp.status_code}"}

        except Exception as e:
            logger.error(f"Live order error: {e}")
            return {"success": False, "error": str(e)}

    def check_resolution(self, market_id: str) -> dict | None:
        """
        Check if a market has resolved.
        Returns resolution data or None if still open.
        """
        try:
            market = self._get_market(market_id)
            if not market:
                return None

            if market.get("closed") or market.get("resolved"):
                tokens = market.get("tokens", [])
                for t in tokens:
                    if t.get("winner"):
                        return {
                            "resolved": True,
                            "winning_side": t.get("outcome", "").lower(),
                            "price": float(t.get("price", 0)),
                        }

            return None
        except Exception as e:
            logger.error(f"Resolution check error: {e}")
            return None

    def get_current_price(self, market_id: str) -> float | None:
        """Get current YES price for a market"""
        return self._get_current_price(market_id)

    def _get_current_price(self, market_id: str) -> float | None:
        try:
            r = self._session.get(
                f"{POLYMARKET_GAMMA_API}/markets/{market_id}",
                timeout=5
            )
            if r.status_code == 200:
                m = r.json()
                prices = json.loads(m.get("outcomePrices", "[0.5,0.5]"))
                return float(prices[0])
        except Exception:
            pass
        return None

    def _get_market(self, market_id: str) -> dict | None:
        try:
            r = self._session.get(
                f"{POLYMARKET_GAMMA_API}/markets/{market_id}",
                timeout=5
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def resolve_paper_trade(self, trade_id: str, winning_side: str) -> dict:
        """Resolve a paper trade and calculate P&L"""
        trade = self._paper_trades.get(trade_id)
        if not trade:
            return {"success": False, "error": "trade_not_found"}

        side = trade["side"]
        amount = trade["amount"]
        shares = trade["shares"]
        price = trade["price"]

        if side == winning_side:
            # Won: payout = shares * 1.0 (each share pays $1 on win)
            payout = shares
            pnl = payout - amount
            outcome = "win"
        else:
            # Lost: lose amount bet
            pnl = -amount
            outcome = "loss"

        self._paper_balance += (amount + pnl)

        trade["status"] = "resolved"
        trade["outcome"] = outcome
        trade["pnl"] = pnl

        return {
            "success": True,
            "outcome": outcome,
            "pnl": round(pnl, 4),
            "payout": round(amount + pnl, 4),
        }

    def get_balance(self) -> float:
        if config.is_paper():
            return self._paper_balance
        # Live: would query Polygon chain
        return 0.0

    def get_status(self) -> dict:
        return {
            "mode": self.mode,
            "balance": self.get_balance(),
            "open_paper_trades": len([t for t in self._paper_trades.values()
                                       if t.get("status") == "open"]),
        }


polymarket_client = PolymarketClient()
