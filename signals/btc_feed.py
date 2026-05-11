"""Real-time BTC price feed via Binance WebSocket + REST"""

import json
import threading
import logging
import time
import requests
from collections import deque
from constants import BINANCE_WS_URL, BINANCE_REST_URL, BTC_SYMBOL

logger = logging.getLogger(__name__)

class BTCPriceFeed:
    def __init__(self):
        self.prices_1m = deque(maxlen=60)   # 60 x 1min candles = 1hr
        self.prices_5m = deque(maxlen=12)   # 12 x 5min candles = 1hr
        self.volumes_1m = deque(maxlen=60)
        self.latest_price = 0.0
        self.latest_volume = 0.0
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        self._running = True
        self._load_history()
        self._thread = threading.Thread(target=self._connect, daemon=True)
        self._thread.start()
        logger.info("✅ BTC price feed started")

    def stop(self):
        self._running = False

    def _load_history(self):
        """Load recent candles from REST API"""
        try:
            r = requests.get(
                f"{BINANCE_REST_URL}/klines",
                params={"symbol": BTC_SYMBOL, "interval": "1m", "limit": 60},
                timeout=10
            )
            candles = r.json()
            with self._lock:
                for c in candles:
                    self.prices_1m.append(float(c[4]))
                    self.volumes_1m.append(float(c[5]))
                if candles:
                    self.latest_price = float(candles[-1][4])

            r5 = requests.get(
                f"{BINANCE_REST_URL}/klines",
                params={"symbol": BTC_SYMBOL, "interval": "5m", "limit": 12},
                timeout=10
            )
            candles5 = r5.json()
            with self._lock:
                for c in candles5:
                    self.prices_5m.append(float(c[4]))

            logger.info(f"✅ Loaded {len(self.prices_1m)} candles, BTC=${self.latest_price:,.2f}")
        except Exception as e:
            logger.error(f"❌ Failed to load BTC history: {e}")

    def _connect(self):
        """Connect via WebSocket for real-time updates"""
        import websocket

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if data.get("e") == "kline":
                    k = data["k"]
                    price = float(k["c"])
                    volume = float(k["v"])
                    is_closed = k["x"]
                    with self._lock:
                        self.latest_price = price
                        self.latest_volume = volume
                        if is_closed:
                            self.prices_1m.append(price)
                            self.volumes_1m.append(volume)
            except Exception as e:
                logger.error(f"WS message error: {e}")

        def on_error(ws, error):
            logger.error(f"WS error: {error}")

        def on_close(ws, *args):
            if self._running:
                logger.warning("WS closed, reconnecting in 5s...")
                time.sleep(5)
                self._connect()

        stream = f"{BTC_SYMBOL.lower()}@kline_1m"
        url = f"{BINANCE_WS_URL}/{stream}"

        ws = websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.run_forever(ping_interval=30)

    def get_signals(self) -> dict:
        """Get current signals snapshot"""
        with self._lock:
            prices = list(self.prices_1m)
            prices5 = list(self.prices_5m)
            volumes = list(self.volumes_1m)
            latest = self.latest_price
            vol = self.latest_volume

        if not prices:
            return {
                "latest": latest,
                "prices": [],
                "volumes": [],
                "momentum_1m": 0.0,
                "momentum_5m": 0.0,
                "momentum_15m": 0.0,
                "momentum_1h": 0.0,
                "volume_ratio": 1.0,
                "rsi": 50.0,
                "trend": "neutral",
            }

        # Momentum calculations
        def momentum(p_list, n):
            if len(p_list) >= n + 1:
                old = p_list[-(n+1)]
                new = p_list[-1]
                if old > 0:
                    return (new - old) / old
            return 0.0

        mom_1m = momentum(prices, 1)
        mom_5m = momentum(prices, 5)
        mom_15m = momentum(prices, 15)
        mom_1h = momentum(prices, 60) if len(prices) >= 60 else momentum(prices, len(prices)-1)

        # Volume ratio
        avg_vol = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else vol
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0

        # RSI (14 period)
        rsi = self._calc_rsi(prices, 14)

        # Trend
        if mom_15m > 0.002:
            trend = "up"
        elif mom_15m < -0.002:
            trend = "down"
        else:
            trend = "neutral"

        return {
            "latest": latest,
            "prices": prices,
            "prices_5m": prices5,
            "volumes": volumes,
            "momentum_1m": round(mom_1m, 5),
            "momentum_5m": round(mom_5m, 5),
            "momentum_15m": round(mom_15m, 5),
            "momentum_1h": round(mom_1h, 5),
            "volume_ratio": round(vol_ratio, 2),
            "rsi": round(rsi, 1),
            "trend": trend,
        }

    def _calc_rsi(self, prices, period=14):
        if len(prices) < period + 1:
            return 50.0
        gains = []
        losses = []
        for i in range(1, period + 1):
            diff = prices[-i] - prices[-(i+1)]
            if diff >= 0:
                gains.append(diff)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(diff))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))


# Global singleton
btc_feed = BTCPriceFeed()
