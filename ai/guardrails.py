"""Validates AI output against market constraints"""

import logging
from constants import MAX_YES_PRICE, MIN_YES_PRICE

logger = logging.getLogger(__name__)

class Guardrails:
    """
    Validates and sanitizes AI decisions before execution.
    Last line of defense before trade placement.
    """

    def validate(self, decision: dict, market: dict, balance: float) -> dict:
        """
        Validate AI decision against hard rules.
        Returns validated decision or skip.
        """
        reasons = []

        # 1. Must have valid action
        action = decision.get("action", "skip")
        if action not in ("buy", "skip"):
            decision["action"] = "skip"
            reasons.append("invalid_action")

        if action == "skip":
            return decision

        # 2. Price guards
        price = market.get("price_yes", 0.5)
        if price > MAX_YES_PRICE:
            decision["action"] = "skip"
            reasons.append(f"price_too_high:{price:.2f}")

        if price < MIN_YES_PRICE:
            decision["action"] = "skip"
            reasons.append(f"price_too_low:{price:.2f}")

        # 3. Confidence minimum
        if decision.get("confidence", 0) < 0.01:
            decision["action"] = "skip"
            reasons.append("confidence_too_low")

        # 4. Edge minimum
        if decision.get("edge", 0) < 0.01:
            decision["action"] = "skip"
            reasons.append("edge_too_small")

        # 5. Balance check
        amount = decision.get("suggested_amount", 0)
        if amount > balance * 0.25:
            decision["suggested_amount"] = balance * 0.10
            reasons.append("amount_capped")

        if amount <= 0:
            decision["action"] = "skip"
            reasons.append("zero_amount")

        # 6. Liquidity check
        liquidity = market.get("liquidity", 0)
        if liquidity < 200:
            decision["action"] = "skip"
            reasons.append(f"low_liquidity:{liquidity:.0f}")

        if reasons:
            logger.debug(f"Guardrails: {reasons}")
            if decision["action"] == "skip":
                decision["reasoning"] = f"Guardrail blocked: {', '.join(reasons)}"

        return decision


guardrails = Guardrails()
