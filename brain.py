"""Brain - Groq AI analysis engine"""

import logging
import db
from ai.groq_client import groq_client
from ai.memory import trading_memory
from ai.guardrails import guardrails
from scoring.calibration import calibration
import config

logger = logging.getLogger(__name__)


class Brain:
    """
    Groq-powered brain that analyzes markets and generates trade theses.
    Uses memory, patterns and calibration for smarter decisions.
    """

    def __init__(self):
        self.analyses_count = 0
        self.skip_count = 0

    def analyze(self, market: dict, signals: dict, balance: float = 10000.0) -> dict:
        """
        Full market analysis pipeline:
        1. Get historical patterns from DB
        2. Retrieve similar memories
        3. Ask Groq for analysis
        4. Calibrate confidence
        5. Validate through guardrails
        """
        # 1. Get historical patterns
        patterns = db.get_top_patterns(limit=5)

        # 2. Get similar past trade memories
        similar = trading_memory.retrieve_similar(
            market.get("question", ""),
            signals,
            top_k=3
        )

        # Add memory context to signals
        enriched_signals = {**signals}
        enriched_signals["similar_trades"] = trading_memory.format_for_prompt(similar)

        # 3. Groq analysis
        decision = groq_client.analyze_market(market, enriched_signals, patterns)

        if not decision:
            return self._skip("Groq returned no response")

        # 4. Calibrate confidence
        if decision.get("action") == "buy":
            raw_conf = decision.get("confidence", 0)
            calibrated_conf = calibration.adjust(
                raw_conf,
                context_key=self._make_context_key(signals)
            )
            decision["confidence"] = calibrated_conf
            decision["confidence_raw"] = raw_conf

        # 5. Guardrails validation
        decision = guardrails.validate(decision, market, balance)

        # Track stats
        if decision.get("action") == "buy":
            self.analyses_count += 1
        else:
            self.skip_count += 1

        return decision

    def _make_context_key(self, signals: dict) -> str:
        """Create context key for calibration lookup"""
        fg = signals.get("fear_greed", 50)
        trend = signals.get("trend", "neutral")
        hour = signals.get("hour_et", 12)

        fg_bucket = "fear" if fg < 40 else "greed" if fg > 60 else "neutral"
        hour_bucket = "morning" if 9 <= hour <= 11 else "midday" if 12 <= hour <= 15 else "other"

        return f"{fg_bucket}_{trend}_{hour_bucket}"

    def record_outcome(self, market_question: str, conditions: dict,
                       reasoning: str, outcome: str, pnl: float):
        """Record trade outcome for future learning"""
        trading_memory.store(
            question=market_question,
            conditions=conditions,
            reasoning=reasoning,
            outcome=outcome,
            pnl=pnl
        )
        # Update calibration based on outcome
        calibration.update(
            context_key=self._make_context_key(conditions),
            was_correct=(outcome == "win")
        )

    def _skip(self, reason: str) -> dict:
        self.skip_count += 1
        return {
            "action": "skip",
            "side": "yes",
            "confidence": 0.0,
            "edge": 0.0,
            "reasoning": reason,
            "suggested_amount": 0.0,
        }

    def get_status(self) -> dict:
        return {
            "analyses": self.analyses_count,
            "skips": self.skip_count,
            "groq": groq_client.get_status(),
            "memory": trading_memory.get_stats(),
        }


brain = Brain()
