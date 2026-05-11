"""
Vector memory store for AI learning from past trades.
Stores trade reasoning and outcomes for context retrieval.
Uses simple TF-IDF similarity (no external dependencies).
"""

import json
import os
import math
import logging
from pathlib import Path
import config

logger = logging.getLogger(__name__)

class TradingMemory:
    """
    Stores past trade reasoning and outcomes.
    Retrieves similar past situations to inform current decisions.
    """

    def __init__(self):
        self.memory_file = config.MEMORY_PATH / "trade_memory.json"
        self.memories = []
        self._load()

    def _load(self):
        try:
            if self.memory_file.exists():
                with open(self.memory_file) as f:
                    self.memories = json.load(f)
                logger.info(f"✅ Loaded {len(self.memories)} memories")
        except Exception as e:
            logger.error(f"Memory load error: {e}")
            self.memories = []

    def _save(self):
        try:
            with open(self.memory_file, "w") as f:
                json.dump(self.memories[-500:], f)  # keep last 500
        except Exception as e:
            logger.error(f"Memory save error: {e}")

    def store(self, question: str, conditions: dict, reasoning: str, outcome: str, pnl: float):
        """Store a trade memory"""
        memory = {
            "question": question,
            "conditions": conditions,
            "reasoning": reasoning,
            "outcome": outcome,
            "pnl": pnl,
            "text": f"{question} {reasoning}",
        }
        self.memories.append(memory)
        self._save()

    def retrieve_similar(self, question: str, conditions: dict, top_k: int = 3) -> list:
        """
        Retrieve most similar past trades using simple keyword matching.
        Returns top_k most relevant memories.
        """
        if not self.memories:
            return []

        query = question.lower()
        query_words = set(query.split())

        scored = []
        for m in self.memories:
            text = m.get("text", "").lower()
            words = set(text.split())

            # Jaccard similarity
            intersection = query_words & words
            union = query_words | words
            similarity = len(intersection) / len(union) if union else 0

            # Boost recent wins
            boost = 1.2 if m.get("outcome") == "win" else 1.0
            score = similarity * boost

            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:top_k]]

    def format_for_prompt(self, memories: list) -> str:
        """Format memories for inclusion in Groq prompt"""
        if not memories:
            return "No similar past trades found."

        lines = []
        for m in memories:
            outcome_icon = "✅" if m["outcome"] == "win" else "❌"
            lines.append(
                f"{outcome_icon} {m['question'][:50]}: "
                f"{m['reasoning'][:80]} → {m['outcome'].upper()} "
                f"(P&L: ${m['pnl']:+.2f})"
            )
        return "\n".join(lines)

    def get_stats(self) -> dict:
        if not self.memories:
            return {"total": 0, "wins": 0, "losses": 0}
        wins = sum(1 for m in self.memories if m.get("outcome") == "win")
        return {
            "total": len(self.memories),
            "wins": wins,
            "losses": len(self.memories) - wins,
        }


trading_memory = TradingMemory()
