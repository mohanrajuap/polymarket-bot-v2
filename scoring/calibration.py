"""Calibration - adjusts AI confidence based on historical accuracy"""

import json
import logging
from pathlib import Path
import config

logger = logging.getLogger(__name__)


class Calibration:
    """
    Tracks AI confidence vs actual win rate.
    Adjusts future confidence estimates based on historical accuracy.

    Example: If AI says 70% confidence but only wins 55% of the time
    in morning/fear conditions → calibrate 70% down to ~55%.
    """

    def __init__(self):
        self.cal_file = config.MEMORY_PATH / "calibration.json"
        self.data = {}  # context_key → {predicted, actual_wins, total}
        self._load()

    def _load(self):
        try:
            if self.cal_file.exists():
                with open(self.cal_file) as f:
                    self.data = json.load(f)
        except Exception as e:
            logger.error(f"Calibration load error: {e}")
            self.data = {}

    def _save(self):
        try:
            with open(self.cal_file, "w") as f:
                json.dump(self.data, f)
        except Exception as e:
            logger.error(f"Calibration save error: {e}")

    def adjust(self, confidence: float, context_key: str) -> float:
        """
        Adjust confidence based on historical accuracy in this context.
        Returns calibrated confidence.
        """
        if context_key not in self.data:
            return confidence  # No data, return as-is

        cal = self.data[context_key]
        total = cal.get("total", 0)

        if total < 10:
            return confidence  # Not enough data

        actual_wr = cal.get("wins", 0) / total
        predicted_wr = cal.get("avg_predicted", confidence)

        # Blend: 70% historical, 30% AI predicted
        if predicted_wr > 0:
            calibration_factor = actual_wr / predicted_wr
            adjusted = confidence * calibration_factor * 0.7 + confidence * 0.3
            return round(min(0.95, max(0.01, adjusted)), 3)

        return confidence

    def update(self, context_key: str, was_correct: bool):
        """Update calibration data after trade outcome"""
        if context_key not in self.data:
            self.data[context_key] = {
                "wins": 0, "total": 0, "avg_predicted": 0.5
            }

        cal = self.data[context_key]
        cal["total"] += 1
        if was_correct:
            cal["wins"] += 1

        self._save()

    def get_accuracy(self, context_key: str) -> float:
        """Get historical accuracy for context"""
        if context_key not in self.data:
            return 0.5
        cal = self.data[context_key]
        total = cal.get("total", 0)
        if total == 0:
            return 0.5
        return cal.get("wins", 0) / total

    def get_all_stats(self) -> dict:
        result = {}
        for key, cal in self.data.items():
            total = cal.get("total", 0)
            wins = cal.get("wins", 0)
            result[key] = {
                "total": total,
                "wins": wins,
                "win_rate": round(wins / total, 3) if total > 0 else 0,
            }
        return result


calibration = Calibration()
