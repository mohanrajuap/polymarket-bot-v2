"""Circuit breaker - kill switch for failures and anomalies"""

import time
import logging
from constants import MAX_API_FAILURES, MAX_CONSECUTIVE_LOSSES, CIRCUIT_BREAK_COOLDOWN
import db

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    Monitors system health and pauses trading when things go wrong.

    Triggers on:
    - Too many API failures
    - Too many consecutive losses
    - Network issues
    - Unusual market behavior
    """

    def __init__(self):
        self.api_failures = 0
        self.consecutive_losses = 0
        self.is_open = False  # True = circuit broken, trading paused
        self._opened_at = 0
        self._trip_reason = ""

    def record_api_failure(self):
        self.api_failures += 1
        if self.api_failures >= MAX_API_FAILURES:
            self.trip(f"Too many API failures: {self.api_failures}")

    def record_api_success(self):
        self.api_failures = max(0, self.api_failures - 1)

    def record_loss(self):
        self.consecutive_losses += 1
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            self.trip(f"Too many consecutive losses: {self.consecutive_losses}")

    def record_win(self):
        self.consecutive_losses = 0

    def trip(self, reason: str):
        if not self.is_open:
            self.is_open = True
            self._opened_at = time.time()
            self._trip_reason = reason
            logger.warning(f"🔴 CIRCUIT BREAKER TRIPPED: {reason}")
            db.log_system_event("CIRCUIT_BREAKER", f"Tripped: {reason}")

    def reset(self):
        self.is_open = False
        self.api_failures = 0
        self.consecutive_losses = 0
        self._trip_reason = ""
        logger.info("🟢 Circuit breaker reset")
        db.log_system_event("CIRCUIT_BREAKER", "Reset")

    def check(self) -> bool:
        """
        Returns True if trading is allowed.
        Auto-resets after cooldown period.
        """
        if not self.is_open:
            return True

        # Auto-reset after cooldown
        elapsed = time.time() - self._opened_at
        if elapsed > CIRCUIT_BREAK_COOLDOWN:
            logger.info(f"Circuit breaker auto-reset after {elapsed:.0f}s cooldown")
            self.reset()
            return True

        remaining = CIRCUIT_BREAK_COOLDOWN - elapsed
        logger.warning(f"Circuit breaker open: {self._trip_reason} — resets in {remaining:.0f}s")
        return False

    def get_status(self) -> dict:
        return {
            "is_open": self.is_open,
            "reason": self._trip_reason,
            "api_failures": self.api_failures,
            "consecutive_losses": self.consecutive_losses,
            "cooldown_remaining": max(0, CIRCUIT_BREAK_COOLDOWN - (time.time() - self._opened_at)) if self.is_open else 0,
        }


circuit_breaker = CircuitBreaker()
