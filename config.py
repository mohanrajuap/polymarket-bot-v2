"""Configuration for Polymarket Bot Arena"""

import os
from pathlib import Path

# ─── Trading Mode ───────────────────────────────────────────
TRADING_MODE = os.environ.get("TRADING_MODE", "paper")  # paper | live

# ─── API Keys ───────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
POLYMARKET_PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")

# ─── Paths ───────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/arena"))
DB_PATH = DATA_DIR / "arena.db"
MEMORY_PATH = DATA_DIR / "memory"
LOG_PATH = DATA_DIR / "logs"

# Create dirs
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_PATH.mkdir(parents=True, exist_ok=True)
LOG_PATH.mkdir(parents=True, exist_ok=True)

# ─── Risk Limits ─────────────────────────────────────────────
PAPER_MAX_POSITION = 50.0
PAPER_MAX_DAILY_LOSS = 200.0
PAPER_STARTING_BALANCE = 10000.0

LIVE_MAX_POSITION = 10.0
LIVE_MAX_DAILY_LOSS = 50.0

# ─── Dashboard ───────────────────────────────────────────────
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = int(os.environ.get("PORT", 8080))
DASHBOARD_USER = "admin"
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "arena2026")

# ─── Feature Flags ───────────────────────────────────────────
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
ORACLE_BRIDGE_ENABLED = True
VECTOR_MEMORY_ENABLED = True
CIRCUIT_BREAKER_ENABLED = True

# ─── Helpers ─────────────────────────────────────────────────
def is_paper():
    return TRADING_MODE == "paper"

def is_live():
    return TRADING_MODE == "live"

def get_max_position():
    return LIVE_MAX_POSITION if is_live() else PAPER_MAX_POSITION

def get_max_daily_loss():
    return LIVE_MAX_DAILY_LOSS if is_live() else PAPER_MAX_DAILY_LOSS
