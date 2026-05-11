"""Supervisor - consensus engine that watches all 5 bots"""

import logging
from constants import (FULL_POSITION_THRESHOLD, HALF_POSITION_THRESHOLD,
                       EVOLUTION_HOURS, MIN_TRADES_FOR_JUDGMENT,
                       MIN_WIN_RATE_TO_SURVIVE, MUTATION_RATE)
import db
import config

logger = logging.getLogger(__name__)


class Supervisor:
    """
    Watches all 5 bots and makes consensus decisions.
    Runs evolution cycle every N hours.
    Implements Hot/Cold path arbitration.
    """

    def __init__(self, bots: list):
        self.bots = {bot.name: bot for bot in bots}
        self.evolution_cycle = 0
        self._last_evolution = 0
        self._active = True

    def get_consensus(self, market: dict, signals: dict, balance: float) -> dict:
        """
        Collect signals from all active bots and build consensus.

        Hot path: 3+ bots agree → full position (fast)
        Cold path: 2 bots agree → half position (cautious)
        No consensus → skip
        """
        if not self._active:
            return {"action": "skip", "reason": "supervisor_stopped"}

        votes = []
        for name, bot in self.bots.items():
            if not bot._active:
                continue
            try:
                # Get learned bias from patterns
                bias = self._get_learned_bias(name)
                decision = bot.make_decision(market, signals, bias)
                if decision["action"] == "buy":
                    votes.append(decision)
                    logger.debug(f"  {name}: BUY conf={decision['confidence']:.2f}")
                else:
                    logger.debug(f"  {name}: SKIP — {decision['reasoning'][:50]}")
            except Exception as e:
                logger.error(f"Bot {name} error: {e}")

        buy_votes = len(votes)
        logger.info(f"Supervisor: {buy_votes}/{len(self.bots)} bots voted BUY")

        if buy_votes == 0:
            return {"action": "skip", "reason": "no_consensus"}

        # Calculate consensus confidence and amount
        avg_confidence = sum(v["confidence"] for v in votes) / len(votes)
        max_amount = max(v["suggested_amount"] for v in votes)

        # Hot path: strong consensus
        if buy_votes >= FULL_POSITION_THRESHOLD:
            return {
                "action": "buy",
                "side": "yes",
                "confidence": min(0.95, avg_confidence * 1.1),
                "suggested_amount": max_amount,
                "vote_count": buy_votes,
                "path": "hot",
                "reasoning": f"HOT: {buy_votes}/{len(self.bots)} bots agree → FULL position",
            }

        # Cold path: partial consensus
        if buy_votes >= HALF_POSITION_THRESHOLD:
            return {
                "action": "buy",
                "side": "yes",
                "confidence": avg_confidence * 0.9,
                "suggested_amount": max_amount * 0.5,
                "vote_count": buy_votes,
                "path": "cold",
                "reasoning": f"COLD: {buy_votes}/{len(self.bots)} bots agree → HALF position",
            }

        return {"action": "skip", "reason": f"weak_consensus_{buy_votes}_votes"}

    def _get_learned_bias(self, bot_name: str) -> float:
        """Get learned bias from pattern database"""
        perf = db.get_bot_performance(bot_name, hours=168)
        wr = perf.get("win_rate", 0.5)
        total = perf.get("total_trades", 0)
        if total < 10:
            return 0.5  # not enough data
        return wr

    def run_evolution(self):
        """
        Evolution cycle: kill worst bot, mutate best bot.
        Only runs every EVOLUTION_HOURS.
        """
        import time
        now = time.time()

        hours_since = (now - self._last_evolution) / 3600
        if hours_since < EVOLUTION_HOURS:
            return

        logger.info(f"🧬 Running Evolution Cycle {self.evolution_cycle + 1}")

        # Get performance for all bots
        performances = []
        for name, bot in self.bots.items():
            perf = db.get_bot_performance(name, hours=EVOLUTION_HOURS)
            if perf["total_trades"] >= MIN_TRADES_FOR_JUDGMENT:
                performances.append((name, perf["win_rate"], perf["total_trades"]))

        if len(performances) < 2:
            logger.info("Not enough data for evolution, skipping")
            return

        # Sort by win rate
        performances.sort(key=lambda x: x[1], reverse=True)
        winner_name, winner_wr, _ = performances[0]
        loser_name, loser_wr, _ = performances[-1]

        logger.info(f"  Winner: {winner_name} ({winner_wr*100:.1f}% WR)")
        logger.info(f"  Loser:  {loser_name} ({loser_wr*100:.1f}% WR)")

        # Only evolve if loser is below survival threshold
        if loser_wr > MIN_WIN_RATE_TO_SURVIVE:
            logger.info("All bots above survival threshold, no evolution needed")
            return

        # Create new bot from winner params
        winner_bot = self.bots[winner_name]
        loser_bot = self.bots[loser_name]

        new_params = loser_bot.mutate(winner_bot.params)
        new_name = f"{loser_bot.strategy_type}-v{loser_bot.generation + 1}"

        # Replace loser with evolved bot
        from bots.strategies import (MomentumBot, MeanRevBot, SentimentBot,
                                      WhaleBot, ContrarianBot)
        strategy_map = {
            "momentum": MomentumBot,
            "mean_reversion": MeanRevBot,
            "sentiment": SentimentBot,
            "whale": WhaleBot,
            "contrarian": ContrarianBot,
        }

        BotClass = strategy_map.get(loser_bot.strategy_type)
        if BotClass:
            new_bot = BotClass(
                name=loser_name,  # keep same slot name
                params=new_params,
                generation=loser_bot.generation + 1,
                lineage=winner_name
            )
            self.bots[loser_name] = new_bot

            db.log_evolution(
                cycle=self.evolution_cycle + 1,
                winner_bot=winner_name,
                loser_bot=loser_name,
                winner_wr=winner_wr,
                loser_wr=loser_wr,
                new_bot_name=new_name,
                winner_params=winner_bot.params
            )

            logger.info(f"✅ Evolution: {loser_name} → {new_name} (inherited from {winner_name})")

        self.evolution_cycle += 1
        self._last_evolution = now

    def stop_bot(self, bot_name: str) -> bool:
        if bot_name in self.bots:
            self.bots[bot_name].stop()
            return True
        return False

    def start_bot(self, bot_name: str) -> bool:
        if bot_name in self.bots:
            self.bots[bot_name].start()
            return True
        return False

    def get_status(self) -> dict:
        return {
            "active": self._active,
            "evolution_cycle": self.evolution_cycle,
            "bots": {
                name: {
                    "active": bot._active,
                    "strategy": bot.strategy_type,
                    "generation": bot.generation,
                }
                for name, bot in self.bots.items()
            }
        }
