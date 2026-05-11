"""Database layer - SQLite for permanent storage"""

import sqlite3
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import config

logger = logging.getLogger(__name__)

def get_conn():
    conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize all database tables"""
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_name TEXT NOT NULL,
        market_id TEXT NOT NULL,
        market_question TEXT,
        side TEXT NOT NULL,
        amount REAL NOT NULL,
        entry_price REAL,
        exit_price REAL,
        outcome TEXT DEFAULT 'pending',
        pnl REAL DEFAULT 0,
        confidence REAL,
        reasoning TEXT,
        trade_id TEXT,
        shares_bought REAL,
        mode TEXT DEFAULT 'paper',
        created_at TEXT DEFAULT (datetime('now')),
        resolved_at TEXT
    );

    CREATE TABLE IF NOT EXISTS conditions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER,
        time_of_day TEXT,
        btc_momentum REAL,
        fear_greed INTEGER,
        market_price REAL,
        volume_ratio REAL,
        rsi REAL,
        macd_signal TEXT,
        outcome TEXT,
        FOREIGN KEY(trade_id) REFERENCES trades(id)
    );

    CREATE TABLE IF NOT EXISTS patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        condition_hash TEXT UNIQUE,
        condition_desc TEXT,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0,
        sample_size INTEGER DEFAULT 0,
        last_updated TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS bot_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_name TEXT NOT NULL,
        win_rate REAL DEFAULT 0,
        total_trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        pnl REAL DEFAULT 0,
        generation INTEGER DEFAULT 0,
        lineage TEXT,
        mode TEXT DEFAULT 'paper',
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS evolution_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle INTEGER,
        winner_bot TEXT,
        loser_bot TEXT,
        winner_win_rate REAL,
        loser_win_rate REAL,
        new_bot_name TEXT,
        winner_params TEXT,
        timestamp TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS open_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER,
        bot_name TEXT,
        market_id TEXT,
        market_question TEXT,
        side TEXT,
        amount REAL,
        entry_price REAL,
        current_price REAL,
        expected_gap REAL,
        shares REAL,
        mode TEXT,
        token_id TEXT,
        opened_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(trade_id) REFERENCES trades(id)
    );

    CREATE TABLE IF NOT EXISTS daily_pnl (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        bot_name TEXT,
        trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        pnl REAL DEFAULT 0,
        mode TEXT DEFAULT 'paper'
    );

    CREATE TABLE IF NOT EXISTS system_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT,
        message TEXT,
        data TEXT,
        timestamp TEXT DEFAULT (datetime('now'))
    );
    """)

    conn.commit()

    # ── Migrations for existing DBs ──────────────────────────
    # Add token_id column to open_positions if it doesn't exist (older DBs).
    try:
        c.execute("ALTER TABLE open_positions ADD COLUMN token_id TEXT")
        conn.commit()
        logger.info("✅ migration: added open_positions.token_id")
    except Exception:
        pass  # column already exists

    conn.close()
    logger.info("✅ Database initialized")

def log_trade(bot_name, market_id, market_question, side, amount,
              entry_price=None, confidence=None, reasoning=None,
              trade_id=None, shares_bought=None, mode="paper"):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades
        (bot_name, market_id, market_question, side, amount,
         entry_price, confidence, reasoning, trade_id, shares_bought, mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (bot_name, market_id, market_question, side, amount,
          entry_price, confidence, reasoning, trade_id, shares_bought, mode))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id

def log_conditions(trade_id, conditions: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO conditions
        (trade_id, time_of_day, btc_momentum, fear_greed,
         market_price, volume_ratio, rsi, macd_signal)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade_id,
        conditions.get("time_of_day"),
        conditions.get("btc_momentum"),
        conditions.get("fear_greed"),
        conditions.get("market_price"),
        conditions.get("volume_ratio"),
        conditions.get("rsi"),
        conditions.get("macd_signal"),
    ))
    conn.commit()
    conn.close()

def resolve_trade(trade_id, outcome, pnl, exit_price=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE trades
        SET outcome=?, pnl=?, exit_price=?,
            resolved_at=datetime('now')
        WHERE id=?
    """, (outcome, pnl, exit_price, trade_id))

    c.execute("""
        UPDATE conditions SET outcome=? WHERE trade_id=?
    """, (outcome, trade_id))

    conn.commit()
    conn.close()

def get_bot_performance(bot_name, hours=12):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses,
            SUM(pnl) as total_pnl,
            AVG(CASE WHEN outcome='win' THEN 1.0 ELSE 0.0 END) as win_rate
        FROM trades
        WHERE bot_name=?
        AND outcome IN ('win','loss')
        AND created_at >= datetime('now', ?)
    """, (bot_name, f'-{hours} hours'))
    row = c.fetchone()
    conn.close()
    return {
        "total_trades": row["total"] or 0,
        "wins": row["wins"] or 0,
        "losses": row["losses"] or 0,
        "total_pnl": row["total_pnl"] or 0.0,
        "win_rate": row["win_rate"] or 0.0,
    }

def get_all_bot_performance():
    from constants import BOT_NAMES
    result = {}
    for name in BOT_NAMES:
        result[name] = get_bot_performance(name, hours=168)
    return result

def get_daily_loss(mode="paper"):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT SUM(pnl) as total_loss
        FROM trades
        WHERE outcome='loss'
        AND mode=?
        AND created_at >= datetime('now', 'start of day')
    """, (mode,))
    row = c.fetchone()
    conn.close()
    val = row["total_loss"]
    return abs(val) if val else 0.0

def get_open_positions():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM open_positions ORDER BY opened_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def add_open_position(trade_id, bot_name, market_id, market_question,
                      side, amount, entry_price, expected_gap, shares, mode,
                      token_id=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO open_positions
        (trade_id, bot_name, market_id, market_question,
         side, amount, entry_price, current_price, expected_gap, shares, mode, token_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (trade_id, bot_name, market_id, market_question,
          side, amount, entry_price, entry_price, expected_gap, shares, mode, token_id))
    conn.commit()
    conn.close()

def remove_open_position(trade_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM open_positions WHERE trade_id=?", (trade_id,))
    conn.commit()
    conn.close()

def update_position_price(trade_id, current_price):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE open_positions SET current_price=? WHERE trade_id=?
    """, (current_price, trade_id))
    conn.commit()
    conn.close()

def log_system_event(event_type, message, data=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO system_events (event_type, message, data)
        VALUES (?, ?, ?)
    """, (event_type, message, json.dumps(data) if data else None))
    conn.commit()
    conn.close()

def get_recent_trades(limit=20, mode=None):
    conn = get_conn()
    c = conn.cursor()
    if mode:
        c.execute("""
            SELECT * FROM trades WHERE mode=?
            ORDER BY created_at DESC LIMIT ?
        """, (mode, limit))
    else:
        c.execute("""
            SELECT * FROM trades
            ORDER BY created_at DESC LIMIT ?
        """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_evolution_history(limit=10):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM evolution_history
        ORDER BY timestamp DESC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def log_evolution(cycle, winner_bot, loser_bot, winner_wr,
                  loser_wr, new_bot_name, winner_params):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO evolution_history
        (cycle, winner_bot, loser_bot, winner_win_rate,
         loser_win_rate, new_bot_name, winner_params)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (cycle, winner_bot, loser_bot, winner_wr,
          loser_wr, new_bot_name, json.dumps(winner_params)))
    conn.commit()
    conn.close()

def get_pending_trades():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM trades WHERE outcome='pending'
        ORDER BY created_at ASC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def update_pattern(condition_hash, condition_desc, outcome):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO patterns (condition_hash, condition_desc, wins, losses, sample_size)
        VALUES (?, ?, 0, 0, 0)
        ON CONFLICT(condition_hash) DO NOTHING
    """, (condition_hash, condition_desc))

    if outcome == "win":
        c.execute("""
            UPDATE patterns SET wins=wins+1, sample_size=sample_size+1,
            win_rate=CAST(wins+1 AS REAL)/(sample_size+1),
            last_updated=datetime('now')
            WHERE condition_hash=?
        """, (condition_hash,))
    else:
        c.execute("""
            UPDATE patterns SET losses=losses+1, sample_size=sample_size+1,
            win_rate=CAST(wins AS REAL)/(sample_size+1),
            last_updated=datetime('now')
            WHERE condition_hash=?
        """, (condition_hash,))

    conn.commit()
    conn.close()

def get_top_patterns(limit=10):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM patterns
        WHERE sample_size >= 5
        ORDER BY win_rate DESC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_worst_patterns(limit=5):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM patterns
        WHERE sample_size >= 5
        ORDER BY win_rate ASC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_daily_pnl_history(days=7):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT
            date(created_at) as date,
            COUNT(*) as trades,
            SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
            SUM(pnl) as pnl
        FROM trades
        WHERE outcome IN ('win','loss')
        AND created_at >= datetime('now', ?)
        GROUP BY date(created_at)
        ORDER BY date ASC
    """, (f'-{days} days',))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_overview():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        SELECT COUNT(*) as total,
        SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
        SUM(pnl) as pnl
        FROM trades WHERE outcome IN ('win','loss')
        AND created_at >= datetime('now', 'start of day')
    """)
    today = dict(c.fetchone())

    c.execute("""
        SELECT COUNT(*) as total,
        SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
        SUM(pnl) as pnl
        FROM trades WHERE outcome IN ('win','loss')
        AND created_at >= datetime('now', '-7 days')
    """)
    week = dict(c.fetchone())

    c.execute("""
        SELECT COUNT(*) as total,
        SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
        SUM(pnl) as pnl
        FROM trades WHERE outcome IN ('win','loss')
    """)
    alltime = dict(c.fetchone())

    conn.close()

    def wr(d):
        t = d.get("total") or 0
        w = d.get("wins") or 0
        return round(w / t * 100, 1) if t > 0 else 0

    return {
        "today": {**today, "win_rate": wr(today)},
        "week": {**week, "win_rate": wr(week)},
        "alltime": {**alltime, "win_rate": wr(alltime)},
    }
