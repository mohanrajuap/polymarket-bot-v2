"""Reconciler - syncs database state with actual market state"""

import logging
import db
from execution.polymarket_client import polymarket_client

logger = logging.getLogger(__name__)


class Reconciler:
    """
    The 'Truth Engine'.
    Periodically checks all open positions against actual market state.
    Catches any discrepancies between DB and reality.
    """

    def __init__(self):
        self.checks_run = 0
        self.discrepancies_fixed = 0

    def reconcile_all(self):
        """Check all pending trades against market state"""
        pending = db.get_pending_trades()
        if not pending:
            return

        logger.info(f"Reconciling {len(pending)} pending trades...")

        for trade in pending:
            try:
                self._reconcile_trade(trade)
            except Exception as e:
                logger.error(f"Reconcile error for trade {trade['id']}: {e}")

        self.checks_run += 1

    def _reconcile_trade(self, trade: dict):
        """Check a single trade's resolution status"""
        market_id = trade["market_id"]
        trade_id = trade["id"]
        created_at = trade["created_at"]

        # Check resolution
        resolution = polymarket_client.check_resolution(market_id)

        if resolution and resolution.get("resolved"):
            winning_side = resolution.get("winning_side", "yes")
            our_side = trade["side"]

            outcome = "win" if our_side == winning_side else "loss"
            amount = trade["amount"]

            if outcome == "win":
                shares = trade.get("shares_bought", amount / 0.51)
                pnl = shares - amount
            else:
                pnl = -amount

            db.resolve_trade(
                trade_id=trade_id,
                outcome=outcome,
                pnl=round(pnl, 4),
                exit_price=resolution.get("price"),
            )

            logger.info(
                f"✅ Reconciled trade {trade_id}: "
                f"{outcome.upper()} P&L=${pnl:+.2f}"
            )
            self.discrepancies_fixed += 1

    def expire_stale_trades(self, hours: int = 2):
        """Mark trades as expired if they've been pending too long"""
        import sqlite3
        import db as db_module
        conn = db_module.get_conn()
        c = conn.cursor()

        c.execute("""
            SELECT id, market_id, amount FROM trades
            WHERE outcome='pending'
            AND created_at < datetime('now', ?)
        """, (f'-{hours} hours',))

        stale = c.fetchall()

        for trade in stale:
            # Try one more resolution check
            resolution = polymarket_client.check_resolution(trade["market_id"])
            if resolution and resolution.get("resolved"):
                continue  # Will be handled by reconcile_all

            # Mark as expired
            c.execute("""
                UPDATE trades SET outcome='expired', pnl=0,
                resolved_at=datetime('now')
                WHERE id=?
            """, (trade["id"],))
            logger.warning(f"⏰ Trade {trade['id']} expired after {hours}h")

        conn.commit()
        conn.close()

    def get_status(self) -> dict:
        return {
            "checks_run": self.checks_run,
            "discrepancies_fixed": self.discrepancies_fixed,
        }


reconciler = Reconciler()
