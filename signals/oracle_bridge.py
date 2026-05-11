"""Oracle bridge - compares market price vs external probability estimates"""

import requests
import logging
import time

logger = logging.getLogger(__name__)

class OracleBridge:
    """
    Compares Polymarket prices against external sources to find mispricings.
    Acts as an additional signal for the brain.
    """

    def __init__(self):
        self._cache = {}
        self._cache_ttl = 300  # 5 min cache

    def get_edge(self, market_question: str, market_price: float) -> dict:
        """
        Estimate true probability from external sources and compare to market.
        Returns edge signal.
        """
        try:
            # Use simple heuristics based on market price position
            # In production, this would query UMA/Chainlink oracles
            edge = self._estimate_edge(market_question, market_price)
            return edge
        except Exception as e:
            logger.error(f"Oracle bridge error: {e}")
            return {"edge": 0.0, "signal": "neutral", "confidence": 0.0}

    def _estimate_edge(self, question: str, price: float) -> dict:
        """
        Estimate edge using price position and question analysis.
        Simple but effective heuristic oracle.
        """
        q = question.lower()

        # BTC up/down 5-min: market is usually efficient near 50c
        # Edge appears when price drifts from 50c without strong momentum
        price_deviation = abs(price - 0.50)

        # If price is between 0.45 and 0.55, it's likely random
        if price_deviation < 0.05:
            return {
                "edge": 0.0,
                "signal": "neutral",
                "confidence": 0.3,
                "oracle_estimate": price,
                "reasoning": "Price near 50c, no oracle edge"
            }

        # If price has deviated, there may be mean reversion opportunity
        if price > 0.60:
            return {
                "edge": round(0.50 - price, 3),
                "signal": "lean_no",
                "confidence": 0.4,
                "oracle_estimate": 0.50,
                "reasoning": f"Price {price:.2f} above fair value, oracle suggests NO"
            }
        elif price < 0.40:
            return {
                "edge": round(0.50 - price, 3),
                "signal": "lean_yes",
                "confidence": 0.4,
                "oracle_estimate": 0.50,
                "reasoning": f"Price {price:.2f} below fair value, oracle suggests YES"
            }

        return {
            "edge": 0.0,
            "signal": "neutral",
            "confidence": 0.2,
            "oracle_estimate": price,
            "reasoning": "No significant oracle deviation"
        }

    def get_btc_fair_value(self, current_price: float, momentum: float) -> float:
        """
        Estimate fair probability for BTC direction based on momentum.
        """
        # Base: 50/50
        fair = 0.50

        # Momentum adjustment: strong momentum → higher YES probability
        if momentum > 0.003:
            fair = min(0.70, 0.50 + momentum * 30)
        elif momentum < -0.003:
            fair = max(0.30, 0.50 + momentum * 30)

        return round(fair, 3)


oracle_bridge = OracleBridge()
