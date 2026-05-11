"""Fear & Greed Index signal"""

import requests
import logging
import time
from constants import FEAR_GREED_API

logger = logging.getLogger(__name__)

class FearGreedFeed:
    def __init__(self):
        self.value = 50
        self.classification = "Neutral"
        self._last_fetch = 0
        self._cache_ttl = 3600  # 1 hour cache

    def get(self) -> dict:
        now = time.time()
        if now - self._last_fetch > self._cache_ttl:
            self._fetch()
        return {
            "value": self.value,
            "classification": self.classification,
            "is_fear": self.value < 40,
            "is_greed": self.value > 60,
            "is_extreme_fear": self.value < 25,
            "is_extreme_greed": self.value > 75,
        }

    def _fetch(self):
        try:
            r = requests.get(f"{FEAR_GREED_API}/?limit=1", timeout=5)
            data = r.json()["data"][0]
            self.value = int(data["value"])
            self.classification = data["value_classification"]
            self._last_fetch = time.time()
            logger.info(f"✅ Fear/Greed: {self.value} ({self.classification})")
        except Exception as e:
            logger.error(f"❌ Fear/Greed fetch failed: {e}")


fear_greed_feed = FearGreedFeed()
