"""Learning engine - records patterns and updates win rates"""

import hashlib
import logging
from datetime import datetime, timezone
import db

logger = logging.getLogger(__name__)


class LearningEngine:
    """
    Bayesian learning engine.
    Records trade conditions and outcomes.
    Updates win rates for each condition set.
    Provides learned bias to bots for future decisions.
    """

    def __init__(self):
        self.updates = 0

    def extract_features(self, signals: dict, market_price: float) -> dict:
        """Extract discrete features from continuous signals"""
        btc_mom = signals.get("momentum_15m", 0)
        fg = signals.get("fear_greed", 50)
        vol_ratio = signals.get("volume_ratio", 1.0)
        rsi = signals.get("rsi", 50)

        # Discretize
        now = datetime.now(timezone.utc)
        hour_et = (now.hour - 5) % 24  # UTC to ET

        features = {
            "time_of_day": self._bucket_hour(hour_et),
            "btc_momentum": self._bucket_momentum(btc_mom),
            "fear_greed": self._bucket_fg(fg),
            "volume": self._bucket_volume(vol_ratio),
            "rsi": self._bucket_rsi(rsi),
            "price": self._bucket_price(market_price),
        }
        return features

    def learn(self, features: dict, outcome: str):
        """Record outcome for a set of features"""
        # Individual features
        for key, value in features.items():
            condition_hash = self._hash(key, value)
            condition_desc = f"{key}={value}"
            db.update_pattern(condition_hash, condition_desc, outcome)

        # Combined feature (most specific)
        combined = "_".join(f"{k}={v}" for k, v in sorted(features.items()))
        combined_hash = hashlib.md5(combined.encode()).hexdigest()[:12]
        db.update_pattern(combined_hash, combined[:100], outcome)

        self.updates += 1

    def get_learned_bias(self, features: dict) -> float:
        """
        Get learned YES bias for current conditions.
        Returns value between 0 and 1 (0.5 = neutral).
        """
        patterns = db.get_top_patterns(limit=20)
        if not patterns:
            return 0.5

        # Score current features against known patterns
        total_weight = 0
        weighted_wr = 0

        for pattern in patterns:
            desc = pattern.get("condition_desc", "")
            wr = pattern.get("win_rate", 0.5)
            sample = pattern.get("sample_size", 0)

            # Check if any feature matches this pattern
            for key, value in features.items():
                if f"{key}={value}" in desc:
                    weight = min(1.0, sample / 20)  # weight by sample size
                    weighted_wr += wr * weight
                    total_weight += weight
                    break

        if total_weight == 0:
            return 0.5

        return min(0.9, max(0.1, weighted_wr / total_weight))

    def _hash(self, key: str, value: str) -> str:
        return hashlib.md5(f"{key}={value}".encode()).hexdigest()[:12]

    def _bucket_hour(self, hour: int) -> str:
        if 9 <= hour <= 11:
            return "morning"
        elif 12 <= hour <= 15:
            return "midday"
        elif 16 <= hour <= 18:
            return "afternoon"
        else:
            return "other"

    def _bucket_momentum(self, mom: float) -> str:
        if mom > 0.003:
            return "strong_up"
        elif mom > 0.001:
            return "up"
        elif mom < -0.003:
            return "strong_down"
        elif mom < -0.001:
            return "down"
        else:
            return "neutral"

    def _bucket_fg(self, fg: int) -> str:
        if fg < 25:
            return "extreme_fear"
        elif fg < 45:
            return "fear"
        elif fg < 55:
            return "neutral"
        elif fg < 75:
            return "greed"
        else:
            return "extreme_greed"

    def _bucket_volume(self, vol: float) -> str:
        if vol > 2.5:
            return "very_high"
        elif vol > 1.5:
            return "high"
        elif vol < 0.7:
            return "low"
        else:
            return "normal"

    def _bucket_rsi(self, rsi: float) -> str:
        if rsi < 30:
            return "oversold"
        elif rsi < 45:
            return "below_mid"
        elif rsi < 55:
            return "neutral"
        elif rsi < 70:
            return "above_mid"
        else:
            return "overbought"

    def _bucket_price(self, price: float) -> str:
        if price < 0.40:
            return "low"
        elif price < 0.48:
            return "below_mid"
        elif price < 0.52:
            return "mid"
        elif price < 0.60:
            return "above_mid"
        else:
            return "high"

    def get_status(self) -> dict:
        top = db.get_top_patterns(limit=5)
        worst = db.get_worst_patterns(limit=3)
        return {
            "updates": self.updates,
            "top_patterns": top,
            "worst_patterns": worst,
        }


learning_engine = LearningEngine()
