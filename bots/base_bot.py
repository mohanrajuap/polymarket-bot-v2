"""Abstract base class for all trading bots"""

import copy
import random
import logging
from abc import ABC, abstractmethod
from constants import MIN_CONFIDENCE, MAX_YES_PRICE, MIN_YES_PRICE, KELLY_CAP, KELLY_MIN
import config
import db

logger = logging.getLogger(__name__)


class BaseBot(ABC):
    """
    Base class all 5 bots inherit from.
    Each bot implements analyze() with its own strategy.
    make_decision() combines all signals into final decision.
    """

    def __init__(self, name: str, strategy_type: str, params: dict,
                 generation: int = 0, lineage: str = None):
        self.name = name
        self.strategy_type = strategy_type
        self.params = params
        self.generation = generation
        self.lineage = lineage or name
        self._active = True

    @abstractmethod
    def analyze(self, market: dict, signals: dict) -> dict:
        """
        Analyze market and signals. Return signal dict:
        {
            "action": "buy" | "hold",
            "side": "yes" | "no",
            "confidence": float,
            "reasoning": str,
        }
        """
        pass

    def make_decision(self, market: dict, signals: dict, learned_bias: float = 0.5) -> dict:
        """
        Combine strategy analysis with signals to make final decision.
        """
        if not self._active:
            return self._skip("Bot is stopped")

        market_price = market.get("price_yes", 0.5)

        # Price guards
        if market_price > MAX_YES_PRICE:
            return self._skip(f"Price too high: {market_price:.2f}")
        if market_price < MIN_YES_PRICE:
            return self._skip(f"Price too low: {market_price:.2f}")

        # Get strategy signal
        signal = self.analyze(market, signals)

        if signal["action"] == "hold":
            return self._skip(signal.get("reasoning", "Strategy says hold"))

        # Confidence from strategy
        confidence = signal.get("confidence", 0)

        # Apply learned bias
        learning_signal = (learned_bias - 0.5) * 0.2
        confidence = min(0.95, confidence + abs(learning_signal))

        # Min confidence check
        min_conf = MIN_CONFIDENCE.get(self.strategy_type, 0.01)
        if confidence < min_conf:
            return self._skip(f"Confidence {confidence:.3f} < {min_conf}")

        # Kelly sizing
        amount = self._kelly_size(market_price, confidence)

        return {
            "action": "buy",
            "side": "yes",
            "confidence": round(confidence, 3),
            "reasoning": signal.get("reasoning", ""),
            "suggested_amount": amount,
            "bot_name": self.name,
        }

    def _kelly_size(self, price: float, confidence: float) -> float:
        """Calculate Kelly criterion bet size"""
        max_pos = config.get_max_position()
        if price <= 0 or price >= 1:
            return max_pos * KELLY_MIN

        # b = payout ratio
        b = (1.0 / price) - 1.0
        # p = our estimated win probability
        p = min(0.95, 0.5 + confidence * 0.3)
        q = 1.0 - p

        f_star = (p * b - q) / b
        if f_star <= 0:
            return 0.0

        f_capped = min(f_star, KELLY_CAP)
        f_capped = max(f_capped, KELLY_MIN)

        return round(max_pos * f_capped, 2)

    def _skip(self, reason: str) -> dict:
        return {
            "action": "skip",
            "side": "yes",
            "confidence": 0.0,
            "reasoning": reason,
            "suggested_amount": 0.0,
            "bot_name": self.name,
        }

    def get_performance(self) -> dict:
        perf = db.get_bot_performance(self.name, hours=168)
        perf["name"] = self.name
        perf["strategy_type"] = self.strategy_type
        perf["generation"] = self.generation
        perf["active"] = self._active
        return perf

    def stop(self):
        self._active = False
        logger.info(f"🔴 {self.name} stopped")

    def start(self):
        self._active = True
        logger.info(f"🟢 {self.name} started")

    def mutate(self, winner_params: dict) -> dict:
        """Create mutated params from winning bot"""
        new_params = copy.deepcopy(winner_params)
        numeric_keys = [k for k, v in new_params.items() if isinstance(v, (int, float))]
        num_mutations = min(3, len(numeric_keys))
        keys = random.sample(numeric_keys, num_mutations) if numeric_keys else []

        for key in keys:
            val = new_params[key]
            delta = val * random.uniform(-0.15, 0.15)
            new_val = val + delta
            if isinstance(val, int):
                new_params[key] = max(1, int(new_val))
            else:
                new_params[key] = max(0.001, round(new_val, 4))

        return new_params

    def export_params(self) -> dict:
        return {
            "name": self.name,
            "strategy_type": self.strategy_type,
            "generation": self.generation,
            "lineage": self.lineage,
            "params": copy.deepcopy(self.params),
        }
