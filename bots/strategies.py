"""All 5 trading bot strategies"""

from bots.base_bot import BaseBot


# ─── Bot 1: Momentum ────────────────────────────────────────
class MomentumBot(BaseBot):
    """
    Trades in direction of short-term BTC price momentum.
    Buys YES when BTC is trending up strongly.
    """
    DEFAULT_PARAMS = {
        "momentum_threshold": 0.0005,
        "lookback_candles": 3,
        "volume_boost_threshold": 1.5,
        "min_confidence": 0.01,
    }

    def __init__(self, name="momentum-v1", params=None, generation=0, lineage=None):
        super().__init__(name, "momentum", params or self.DEFAULT_PARAMS.copy(), generation, lineage)

    def analyze(self, market: dict, signals: dict) -> dict:
        prices = signals.get("prices", [])
        n = self.params["lookback_candles"]

        if len(prices) < n + 1:
            return {"action": "hold", "confidence": 0, "reasoning": "insufficient price data"}

        old = prices[-(n+1)]
        new = prices[-1]
        if old == 0:
            return {"action": "hold", "confidence": 0, "reasoning": "zero price"}

        momentum = (new - old) / old
        threshold = self.params["momentum_threshold"]

        if abs(momentum) < threshold:
            return {
                "action": "hold",
                "confidence": 0,
                "reasoning": f"Momentum {momentum*100:.3f}% below threshold {threshold*100:.3f}%"
            }

        # Trend strength: how many candles moved same direction?
        recent = prices[-n:]
        consecutive = sum(1 for i in range(1, len(recent)) if
                         (momentum > 0 and recent[i] > recent[i-1]) or
                         (momentum < 0 and recent[i] < recent[i-1]))
        trend_strength = consecutive / max(len(recent) - 1, 1)

        # Volume boost
        vol_ratio = signals.get("volume_ratio", 1.0)
        vol_boost = min(1.2, vol_ratio / self.params["volume_boost_threshold"])

        confidence = trend_strength * 0.7 + vol_boost * 0.3
        side = "yes" if momentum > 0 else "no"

        return {
            "action": "buy",
            "side": side,
            "confidence": min(0.9, confidence),
            "reasoning": f"Momentum {momentum*100:.3f}% trend={trend_strength:.2f} vol={vol_ratio:.1f}x",
        }


# ─── Bot 2: Mean Reversion ───────────────────────────────────
class MeanRevBot(BaseBot):
    """
    Bets against overextended BTC moves using RSI.
    Enters when RSI indicates overbought/oversold.
    """
    DEFAULT_PARAMS = {
        "rsi_oversold": 35,
        "rsi_overbought": 65,
        "momentum_extreme": 0.008,
        "min_confidence": 0.01,
    }

    def __init__(self, name="meanrev-v1", params=None, generation=0, lineage=None):
        super().__init__(name, "mean_reversion", params or self.DEFAULT_PARAMS.copy(), generation, lineage)

    def analyze(self, market: dict, signals: dict) -> dict:
        rsi = signals.get("rsi", 50)
        momentum = signals.get("momentum_15m", 0)
        price = market.get("price_yes", 0.5)

        # RSI oversold + price low = good YES entry
        if rsi < self.params["rsi_oversold"] and price < 0.52:
            confidence = (self.params["rsi_oversold"] - rsi) / self.params["rsi_oversold"]
            return {
                "action": "buy",
                "side": "yes",
                "confidence": min(0.8, confidence),
                "reasoning": f"RSI oversold {rsi:.1f}, price {price:.2f} → mean reversion up",
            }

        # Extreme momentum = might reverse
        if abs(momentum) > self.params["momentum_extreme"]:
            confidence = min(0.6, abs(momentum) * 30)
            return {
                "action": "buy",
                "side": "yes" if momentum < 0 else "no",
                "confidence": confidence,
                "reasoning": f"Extreme momentum {momentum*100:.3f}% → reversion expected",
            }

        return {
            "action": "hold",
            "confidence": 0,
            "reasoning": f"RSI {rsi:.1f} neutral, no reversion signal",
        }


# ─── Bot 3: Sentiment ────────────────────────────────────────
class SentimentBot(BaseBot):
    """
    Trades based on Fear/Greed index and market sentiment.
    Extreme fear = contrarian buy, extreme greed = caution.
    """
    DEFAULT_PARAMS = {
        "fear_threshold": 30,
        "greed_threshold": 70,
        "momentum_confirm": 0.001,
        "min_confidence": 0.01,
    }

    def __init__(self, name="sentiment-v1", params=None, generation=0, lineage=None):
        super().__init__(name, "sentiment", params or self.DEFAULT_PARAMS.copy(), generation, lineage)

    def analyze(self, market: dict, signals: dict) -> dict:
        fg = signals.get("fear_greed", 50)
        momentum = signals.get("momentum_15m", 0)
        price = market.get("price_yes", 0.5)

        # Extreme fear + any upward momentum = buy YES
        if fg < self.params["fear_threshold"]:
            if momentum > self.params["momentum_confirm"]:
                confidence = (self.params["fear_threshold"] - fg) / self.params["fear_threshold"] * 0.8
                return {
                    "action": "buy",
                    "side": "yes",
                    "confidence": min(0.85, confidence),
                    "reasoning": f"Extreme fear {fg} + upward momentum → bullish reversal",
                }

        # Moderate fear + price near 50 = slight YES lean
        if fg < 45 and 0.45 <= price <= 0.55:
            confidence = 0.15 + (45 - fg) / 100
            return {
                "action": "buy",
                "side": "yes",
                "confidence": min(0.5, confidence),
                "reasoning": f"Fear {fg} + neutral price {price:.2f} → mild YES lean",
            }

        return {
            "action": "hold",
            "confidence": 0,
            "reasoning": f"Sentiment neutral: Fear/Greed {fg}",
        }


# ─── Bot 4: Whale Follower ───────────────────────────────────
class WhaleBot(BaseBot):
    """
    Follows order flow signals and large volume movements.
    Bets with the direction of large market participants.
    """
    DEFAULT_PARAMS = {
        "volume_whale_threshold": 2.5,
        "price_shift_threshold": 0.02,
        "momentum_confirm": 0.0005,
        "min_confidence": 0.01,
    }

    def __init__(self, name="whale-v1", params=None, generation=0, lineage=None):
        super().__init__(name, "whale", params or self.DEFAULT_PARAMS.copy(), generation, lineage)

    def analyze(self, market: dict, signals: dict) -> dict:
        vol_ratio = signals.get("volume_ratio", 1.0)
        momentum = signals.get("momentum_5m", 0)
        price = market.get("price_yes", 0.5)

        # Whale volume spike in direction of momentum
        if vol_ratio > self.params["volume_whale_threshold"]:
            if momentum > self.params["momentum_confirm"]:
                confidence = min(0.8, (vol_ratio / self.params["volume_whale_threshold"]) * 0.4)
                return {
                    "action": "buy",
                    "side": "yes",
                    "confidence": confidence,
                    "reasoning": f"Whale volume {vol_ratio:.1f}x + momentum {momentum*100:.3f}% → follow whales",
                }

        # Large price shift suggests informed trading
        if abs(momentum) > self.params["price_shift_threshold"]:
            side = "yes" if momentum > 0 else "no"
            confidence = min(0.7, abs(momentum) * 20)
            return {
                "action": "buy",
                "side": side,
                "confidence": confidence,
                "reasoning": f"Large price shift {momentum*100:.2f}% → whale activity",
            }

        return {
            "action": "hold",
            "confidence": 0,
            "reasoning": f"No whale signals: vol={vol_ratio:.1f}x mom={momentum*100:.3f}%",
        }


# ─── Bot 5: Contrarian ───────────────────────────────────────
class ContrarianBot(BaseBot):
    """
    Bets against extreme market moves.
    Fades overreactions and extreme crowd behavior.
    """
    DEFAULT_PARAMS = {
        "extreme_momentum_threshold": 0.006,
        "extreme_volume_threshold": 3.0,
        "price_extreme_high": 0.65,
        "price_extreme_low": 0.35,
        "min_confidence": 0.01,
    }

    def __init__(self, name="contrarian-v1", params=None, generation=0, lineage=None):
        super().__init__(name, "contrarian", params or self.DEFAULT_PARAMS.copy(), generation, lineage)

    def analyze(self, market: dict, signals: dict) -> dict:
        momentum = signals.get("momentum_15m", 0)
        vol_ratio = signals.get("volume_ratio", 1.0)
        rsi = signals.get("rsi", 50)
        price = market.get("price_yes", 0.5)

        # Extreme downward move + extreme volume = contrarian YES
        if (momentum < -self.params["extreme_momentum_threshold"] and
                vol_ratio > self.params["extreme_volume_threshold"] and
                price < 0.55):
            confidence = min(0.7, abs(momentum) * 40 + (vol_ratio - 3) * 0.1)
            return {
                "action": "buy",
                "side": "yes",
                "confidence": confidence,
                "reasoning": f"Extreme drop {momentum*100:.2f}% vol={vol_ratio:.1f}x → contrarian YES",
            }

        # RSI extreme + price neutral = contrarian
        if rsi < 30 and price < 0.55:
            confidence = (30 - rsi) / 30 * 0.6
            return {
                "action": "buy",
                "side": "yes",
                "confidence": min(0.65, confidence),
                "reasoning": f"RSI extreme {rsi:.0f} → contrarian reversal expected",
            }

        return {
            "action": "hold",
            "confidence": 0,
            "reasoning": "No contrarian signal",
        }
