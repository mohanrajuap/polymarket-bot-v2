"""FastAPI dashboard backend for Bond Bot"""

import secrets
import logging
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import config
import db

logger = logging.getLogger(__name__)

app = FastAPI(title="Polymarket Bond Bot")
security = HTTPBasic()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Global refs
_bots = []
_executor = None
_exit_monitor = None
_circuit_breaker = None
_brain = None
_scanner = None


def set_refs(bots, executor, exit_monitor, circuit_breaker, brain, scanner):
    global _bots, _executor, _exit_monitor, _circuit_breaker, _brain, _scanner
    _bots = bots or []
    _executor = executor
    _exit_monitor = exit_monitor
    _circuit_breaker = circuit_breaker
    _brain = brain
    _scanner = scanner


def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_user = secrets.compare_digest(credentials.username, config.DASHBOARD_USER)
    correct_pass = secrets.compare_digest(credentials.password, config.DASHBOARD_PASS)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@app.get("/api/overview", dependencies=[Depends(verify_auth)])
def get_overview():
    return db.get_overview()


@app.get("/api/bots", dependencies=[Depends(verify_auth)])
def get_bots():
    bots_list = _bots if isinstance(_bots, list) else []
    result = {}
    for bot in bots_list:
        try:
            if hasattr(bot, "get_status"):
                result[bot.name] = bot.get_status()
            elif hasattr(bot, "name"):
                result[bot.name] = {"name": bot.name, "active": getattr(bot, "_active", True)}
        except Exception:
            pass
    return result


@app.post("/api/bots/{bot_name}/stop", dependencies=[Depends(verify_auth)])
def stop_bot(bot_name: str):
    bots_list = _bots if isinstance(_bots, list) else []
    for bot in bots_list:
        if getattr(bot, "name", None) == bot_name:
            if hasattr(bot, "stop"): bot.stop()
            else: bot._active = False
            active = [b.name for b in bots_list if getattr(b, "_active", False) and b.name != bot_name]
            return {"success": True, "bot": bot_name, "status": "stopped",
                    "message": f"Work handed to: {', '.join(active)}" if active else "No active bots"}
    raise HTTPException(404, f"Bot {bot_name} not found")


@app.post("/api/bots/{bot_name}/start", dependencies=[Depends(verify_auth)])
def start_bot(bot_name: str):
    bots_list = _bots if isinstance(_bots, list) else []
    for bot in bots_list:
        if getattr(bot, "name", None) == bot_name:
            if hasattr(bot, "start"): bot.start()
            else: bot._active = True
            return {"success": True, "bot": bot_name, "status": "started"}
    raise HTTPException(404, f"Bot {bot_name} not found")


@app.get("/api/radar", dependencies=[Depends(verify_auth)])
def get_radar():
    if not _scanner or not hasattr(_scanner, "get_radar"):
        return []
    try:
        radar = _scanner.get_radar()
        bots_list = _bots if isinstance(_bots, list) else []
        result = []
        for bond in radar[:20]:
            assigned_bot = None
            for bot in bots_list:
                if getattr(bot, "_active", False) and hasattr(bot, "can_trade") and bot.can_trade(bond):
                    assigned_bot = bot.name
                    break
            result.append({
                **bond,
                "assigned_bot": assigned_bot or "no_bot_available",
                "will_trade": assigned_bot is not None,
            })
        return result
    except Exception as e:
        return []


@app.get("/api/prices", dependencies=[Depends(verify_auth)])
def get_prices():
    if not _scanner or not hasattr(_scanner, "get_prices"):
        return {"BTC": 0, "ETH": 0, "SOL": 0, "GOLD": 0, "OIL": 0, "SPX": 0}
    try:
        return _scanner.get_prices()
    except Exception:
        return {"BTC": 0, "ETH": 0, "SOL": 0}


@app.get("/api/trades", dependencies=[Depends(verify_auth)])
def get_trades(limit: int = 20):
    return db.get_recent_trades(limit=limit)


@app.get("/api/positions", dependencies=[Depends(verify_auth)])
def get_positions():
    return db.get_open_positions()


@app.get("/api/patterns", dependencies=[Depends(verify_auth)])
def get_patterns():
    return {"best": db.get_top_patterns(limit=5), "worst": db.get_worst_patterns(limit=3)}


@app.get("/api/pnl/daily", dependencies=[Depends(verify_auth)])
def get_daily_pnl():
    return db.get_daily_pnl_history(days=14)


@app.get("/api/status", dependencies=[Depends(verify_auth)])
def get_status():
    bots_list = _bots if isinstance(_bots, list) else []
    return {
        "mode": config.TRADING_MODE,
        "circuit_breaker": _circuit_breaker.get_status() if _circuit_breaker else {},
        "executor": _executor.get_status() if _executor else {},
        "exit_monitor": _exit_monitor.get_status() if _exit_monitor else {},
        "active_bots": sum(1 for b in bots_list if getattr(b, "_active", False)),
        "total_bots": len(bots_list),
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": config.TRADING_MODE}


@app.get("/", dependencies=[Depends(verify_auth)])
def serve_dashboard():
    return FileResponse("dashboard/index.html")


@app.post("/api/bots/{bot_name}/amount", dependencies=[Depends(verify_auth)])
def set_bot_amount(bot_name: str, payload: dict):
    """Set per-trade fixed amount for a bot"""
    bots_list = _bots if isinstance(_bots, list) else []
    amount = float(payload.get("amount", 0))
    if amount <= 0:
        raise HTTPException(400, "Amount must be greater than 0")
    for bot in bots_list:
        if getattr(bot, "name", None) == bot_name:
            bot._fixed_amount = amount
            return {
                "success": True,
                "bot": bot_name,
                "fixed_amount": amount,
                "message": f"${amount:.2f} per trade set for {bot_name}"
            }
    raise HTTPException(404, f"Bot {bot_name} not found")


@app.post("/api/telegram/test", dependencies=[Depends(verify_auth)])
def test_telegram():
    """Send a test message to Telegram"""
    from notifier import notifier
    success = notifier.test()
    if success:
        return {"success": True, "message": "Test message sent to Telegram!"}
    return {"success": False, "message": "Failed - check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"}


@app.get("/api/telegram/status", dependencies=[Depends(verify_auth)])
def telegram_status():
    """Check if Telegram is configured"""
    from notifier import notifier
    return {
        "enabled": notifier.enabled,
        "token_set": bool(notifier.token),
        "chat_id_set": bool(notifier.chat_id),
    }
