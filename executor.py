"""Executor - places trades on Polymarket"""

import logging
import db
import config
from execution.polymarket_client import polymarket_client
from risk.circuit_breaker import circuit_breaker
from constants import MAX_OPEN_POSITIONS

logger = logging.getLogger(__name__)


class Executor:
    """
    Receives approved trade decisions and executes them.
    Handles paper and live modes.
    Enforces risk limits before execution.
    """

    def __init__(self):
        self.trades_placed = 0
        self.trades_rejected = 0

    def execute(self, decision: dict, market: dict) -> dict:
        """
        Execute a trade decision.
        Returns result dict.
        """
        # Circuit breaker check
        if not circuit_breaker.check():
            self.trades_rejected += 1
            return {"success": False, "reason": "circuit_breaker_open"}

        # Daily loss check
        daily_loss = db.get_daily_loss(config.TRADING_MODE)
        max_loss = config.get_max_daily_loss()
        if daily_loss >= max_loss:
            self.trades_rejected += 1
            logger.warning(f"Daily loss limit hit: ${daily_loss:.2f} >= ${max_loss:.2f}")
            return {"success": False, "reason": "daily_loss_limit"}

        # Open positions check
        open_positions = db.get_open_positions()
        if len(open_positions) >= MAX_OPEN_POSITIONS:
            self.trades_rejected += 1
            return {"success": False, "reason": "max_positions_reached"}

        # Get trade params
        side = decision.get("side", "yes")
        amount = decision.get("suggested_amount", 0)
        bot_name = decision.get("bot_name", "supervisor")

        if amount <= 0:
            return {"success": False, "reason": "zero_amount"}

        market_id = market.get("id")
        market_question = market.get("question", "")

        # Place order
        result = polymarket_client.place_order(
            market_id=market_id,
            side=side,
            amount=amount,
            market_question=market_question,
        )

        if not result.get("success"):
            circuit_breaker.record_api_failure()
            self.trades_rejected += 1
            logger.error(f"Trade failed: {result.get('error')}")
            return result

        circuit_breaker.record_api_success()

        # Use the ACTUAL fill price from the broker, not the indicative price
        # passed in via the market dict (which only carried a hint)
        entry_price = result.get("price") or market.get("price_yes", 0.5)

        # Log to database
        trade_db_id = db.log_trade(
            bot_name=bot_name,
            market_id=market_id,
            market_question=market_question,
            side=side,
            amount=amount,
            entry_price=entry_price,
            confidence=decision.get("confidence"),
            reasoning=decision.get("reasoning"),
            trade_id=result.get("trade_id"),
            shares_bought=result.get("shares"),
            mode=config.TRADING_MODE,
        )

        # Add to open positions
        expected_gap = decision.get("edge", 0.05)
        db.add_open_position(
            trade_id=trade_db_id,
            bot_name=bot_name,
            market_id=market_id,
            market_question=market_question,
            side=side,
            amount=amount,
            entry_price=entry_price,
            expected_gap=expected_gap,
            shares=result.get("shares", 0),
            mode=config.TRADING_MODE,
            token_id=market.get("token_id"),
        )

        self.trades_placed += 1

        logger.info(
            f"✅ TRADE PLACED: {side.upper()} ${amount:.2f} "
            f"@ {entry_price:.3f} | {market_question[:50]}"
        )

        return {
            "success": True,
            "trade_db_id": trade_db_id,
            "trade_id": result.get("trade_id"),
            "amount": amount,
            "price": entry_price,
        }

    def get_status(self) -> dict:
        return {
            "trades_placed": self.trades_placed,
            "trades_rejected": self.trades_rejected,
            "balance": polymarket_client.get_balance(),
        }


executor = Executor()
