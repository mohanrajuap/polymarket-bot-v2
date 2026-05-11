"""High-speed momentum delta - bypasses AI for stop decisions"""

import threading
import logging
from collections import deque
import time

logger = logging.getLogger(__name__)

class MomentumDelta:
    """
    High-speed price action monitor.
    Bypasses AI for emergency stop decisions.
    Runs independently from main brain loop.
    """

    def __init__(self):
        self.deltas = deque(maxlen=10)
        self.alert_threshold = 0.005  # 0.5% sudden move
        self._callbacks = []
        self._lock = threading.Lock()

    def update(self, price: float, prev_price: float):
        if prev_price <= 0:
            return
        delta = (price - prev_price) / prev_price
        with self._lock:
            self.deltas.append({
                "delta": delta,
                "price": price,
                "timestamp": time.time()
            })
            # Fire alert if sudden large move
            if abs(delta) > self.alert_threshold:
                self._fire_alert(delta, price)

    def _fire_alert(self, delta: float, price: float):
        direction = "UP" if delta > 0 else "DOWN"
        logger.warning(f"⚡ MOMENTUM ALERT: BTC {direction} {delta*100:.2f}% to ${price:,.2f}")
        for cb in self._callbacks:
            try:
                cb(delta, price)
            except Exception as e:
                logger.error(f"Momentum alert callback error: {e}")

    def register_alert(self, callback):
        """Register callback for sudden momentum events"""
        self._callbacks.append(callback)

    def get_current(self) -> dict:
        with self._lock:
            deltas = list(self.deltas)

        if not deltas:
            return {"momentum": 0.0, "acceleration": 0.0, "alert": False}

        recent = [d["delta"] for d in deltas[-3:]]
        avg = sum(recent) / len(recent) if recent else 0.0

        # Acceleration: is momentum speeding up?
        accel = 0.0
        if len(deltas) >= 2:
            accel = deltas[-1]["delta"] - deltas[-2]["delta"]

        return {
            "momentum": round(avg, 5),
            "acceleration": round(accel, 5),
            "alert": abs(avg) > self.alert_threshold,
            "direction": "up" if avg > 0 else "down" if avg < 0 else "flat",
        }


momentum_delta = MomentumDelta()
