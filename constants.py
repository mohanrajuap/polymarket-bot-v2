"""Global constants for Polymarket BTC 5-Min Bot"""

# API Endpoints
POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_API = "https://clob.polymarket.com"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
BINANCE_REST_URL = "https://api.binance.com/api/v3"
FEAR_GREED_API = "https://api.alternative.me/fng"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Market identification
BTC_SYMBOL = "BTCUSDT"
SOL_SYMBOL = "SOLUSDT"
MARKET_KEYWORDS = ["bitcoin", "btc", "up or down"]
MIN_LIQUIDITY = 100.0
MIN_VOLUME_24H = 50.0

# ─── BTC 5-MIN STRATEGY PARAMETERS ─────────────────────────
# Market lifecycle: opens at t=0s, expires at t=300s (5 minutes)
#
# Timeline:
#   t=0s ──────── t=90s ─────────────── t=210s ──── t=300s
#   [opens]       [3:30 remaining]      [1:30 rem]  [expires]
#      ↑              ↑                     ↑
#      Entry          Last second           FORCE CLOSE
#      window         to enter              at current price
#      (0 → 90s)      (after this:          (no holds past
#                      ignore market)        this point)
#
# Profit-take fires anytime in [0, FORCE_CLOSE) when price ≥ EXIT_PRICE_TARGET.
# At t = FORCE_CLOSE_SEC the bot closes the position no matter what — win or loss.
ENTRY_WINDOW_START_SEC = 0      # Earliest second after market open we'll buy
ENTRY_WINDOW_END_SEC = 90       # Latest second to enter (3:30 remaining)
FORCE_CLOSE_SEC = 210           # Force close at this elapsed time (1:30 remaining)
EXIT_WINDOW_END_SEC = FORCE_CLOSE_SEC  # legacy alias for any code reading this
WINDOW_DURATION_SEC = 300       # Total market lifetime (5 min)

# Price thresholds (Polymarket prices are 0.0 to 1.0)
ENTRY_PRICE_MAX = 0.20          # Buy a side only if its price is at or below this
EXIT_PRICE_TARGET = 0.30        # Sell when price climbs to or above this
EXIT_STOP_LOSS = 0.0            # Optional safety stop (0 = disabled).
                                # Set e.g. 0.05 to sell if the side drops to 5¢.

# Position sizing (paper)
PAPER_TRADE_SIZE_USD = 2.0      # Fixed $2 per trade in paper mode

# Polling intervals
PRICE_POLL_INTERVAL_SEC = 1     # Check price every 1 second
MARKET_DISCOVERY_INTERVAL_SEC = 2   # How often to re-discover the active market

# Bot names — each watches BTC differently
BOT_NAMES = [
    "btc-yes-only",
    "btc-no-only",
    "btc-either-side",
]
SUPERVISOR_NAME = "supervisor"

# Exit triggers (legacy, kept for compatibility)
TARGET_HIT_PCT = 0.85
VOLUME_SPIKE_MULTIPLIER = 3.0
STALE_HOURS = 24
STALE_PRICE_CHANGE = 0.02
STOP_LOSS_PCT = 0.30

# Learning
EVOLUTION_HOURS = 6
MIN_TRADES_FOR_JUDGMENT = 10
MIN_WIN_RATE_TO_SURVIVE = 0.50
MUTATION_RATE = 0.15
LEARNING_WEIGHT_MAX = 0.30

# Risk
MAX_DAILY_LOSS_PAPER = 200.0
MAX_DAILY_LOSS_LIVE = 50.0
MAX_POSITION_PAPER = 50.0
MAX_POSITION_LIVE = 10.0
MAX_TRADES_PER_HOUR = 60
MAX_OPEN_POSITIONS = 10

# Groq
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_MAX_TOKENS = 500
GROQ_TEMPERATURE = 0.1

# Timing
SCAN_INTERVAL_SECONDS = 5
POSITION_CHECK_INTERVAL = 1
EVOLUTION_CHECK_INTERVAL = 300

# Circuit breaker
MAX_API_FAILURES = 5
MAX_CONSECUTIVE_LOSSES = 10
CIRCUIT_BREAK_COOLDOWN = 300

# Confidence thresholds (legacy)
MIN_CONFIDENCE = {
    "momentum": 0.01,
    "mean_reversion": 0.01,
    "sentiment": 0.01,
    "whale": 0.01,
    "contrarian": 0.01,
}

# Price guards
MAX_YES_PRICE = 0.72
MIN_YES_PRICE = 0.35

# Telegram
TELEGRAM_ENABLED = False

# Database
DB_FILE = "arena.db"
MEMORY_DIR = "memory"
