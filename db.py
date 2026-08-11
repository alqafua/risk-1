"""
db.py - قاعدة البيانات الكاملة مع سحب الربح التلقائي
"""
import sqlite3
import json
import os
import threading
from datetime import datetime, date

DB_PATH = os.environ.get("DATABASE_PATH", "risk_guard.db")
_lock = threading.Lock()

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

DEFAULT_SETTINGS = {
    "sl_auto_enabled": ("true", True),
    "risk_per_trade_pct": ("1.0", True),
    "dca_recalc_enabled": ("true", True),
    "daily_loss_limit_enabled": ("true", True),
    "daily_loss_limit_pct": ("5.0", True),
    "daily_loss_hard_stop": ("true", True),
    "cooldown_enabled": ("true", True),
    "cooldown_minutes": ("30", True),
    "cooldown_force_close": ("true", True),
    "max_daily_trades_enabled": ("true", True),
    "max_daily_trades": ("5", True),
    "max_concurrent_enabled": ("true", True),
    "max_concurrent_positions": ("3", True),
    "max_exposure_enabled": ("true", True),
    "max_exposure_pct": ("300", True),
    "telegram_notifications_enabled": ("true", True),
    "journal_enabled": ("true", True),
    "dead_man_switch_enabled": ("true", True),
    "heartbeat_timeout_minutes": ("5", True),
    "volatility_guard_enabled": ("true", True),
    "volatility_threshold_pct": ("3.0", True),
    "max_leverage_enabled": ("true", True),
    "max_leverage": ("20", True),
    "loss_streak_enabled": ("true", True),
    "loss_streak_count": ("3", True),
    "loss_streak_lock_minutes": ("120", True),
    "time_restriction_enabled": ("false", True),
    "time_restriction_start": ("00:00", True),
    "time_restriction_end": ("06:00", True),
    "weekly_report_enabled": ("true", True),
    "kill_switch_active": ("false", True),
    "correlation_guard_enabled": ("true", True),
    "paper_mode_enabled": ("true", True),
    "account_equity_override": ("0", True),
    "profit_withdrawal_enabled": ("false", True),
    "profit_withdrawal_base_capital": ("1000", True),
    "withdrawal_api_key": ("", True),
    "withdrawal_api_secret": ("", True),
}

def init_db():
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS positions_tracked (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL,
            avg_entry_price REAL,
            quantity REAL,
            leverage INTEGER,
            sl_price REAL,
            sl_order_id TEXT,
            opened_at TEXT,
            closed_at TEXT,
            status TEXT DEFAULT 'open',
            is_dca INTEGER DEFAULT 0,
            realized_pnl REAL DEFAULT 0
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            realized_pnl REAL DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            concurrent_max INTEGER DEFAULT 0,
            hard_stop_triggered INTEGER DEFAULT 0
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS journal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            symbol TEXT,
            details TEXT,
            notified INTEGER DEFAULT 0
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS loss_streak (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            consecutive_losses INTEGER DEFAULT 0,
            last_loss_at TEXT,
            locked_until TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS cooldown_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            locked_until TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS heartbeat (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_beat TEXT
        )""")
        conn.commit()
        for key, (value, enabled) in DEFAULT_SETTINGS.items():
            cur.execute("SELECT 1 FROM settings WHERE key=?", (key,))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO settings (key, value, enabled, updated_at) VALUES (?,?,?,?)",
                    (key, value, int(enabled), datetime.utcnow().isoformat()),
                )
        cur.execute("INSERT OR IGNORE INTO loss_streak (id, consecutive_losses) VALUES (1, 0)")
        cur.execute("INSERT OR IGNORE INTO cooldown_state (id, locked_until) VALUES (1, NULL)")
        cur.execute("INSERT OR IGNORE INTO heartbeat (id, last_beat) VALUES (1, NULL)")
        conn.commit()
        conn.close()

def get_all_settings():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM settings ORDER BY key").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_setting(key, cast=str):
    conn = get_conn()
    row = conn.execute("SELECT value, enabled FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if not row:
        return None, False
    val = row["value"]
    try:
        if cast == bool:
            val = str(val).lower() == "true"
        elif cast == int:
            val = int(float(val))
        elif cast == float:
            val = float(val)
    except (ValueError, TypeError):
        pass
    return val, bool(row["enabled"])

def update_setting(key, value=None, enabled=None):
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        if value is not None and enabled is not None:
            cur.execute(
                "UPDATE settings SET value=?, enabled=?, updated_at=? WHERE key=?",
                (str(value), int(enabled), datetime.utcnow().isoformat(), key),
            )
        elif value is not None:
            cur.execute(
                "UPDATE settings SET value=?, updated_at=? WHERE key=?",
                (str(value), datetime.utcnow().isoformat(), key),
            )
        elif enabled is not None:
            cur.execute(
                "UPDATE settings SET enabled=?, updated_at=? WHERE key=?",
                (int(enabled), datetime.utcnow().isoformat(), key),
            )
        conn.commit()
        conn.close()

def log_event(event_type, symbol=None, details=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO journal_events (timestamp, event_type, symbol, details) VALUES (?,?,?,?)",
        (datetime.utcnow().isoformat(), event_type, symbol, json.dumps(details or {}, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

def get_recent_events(limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM journal_events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def _today():
    return date.today().isoformat()

def get_today_stats():
    conn = get_conn()
    row = conn.execute("SELECT * FROM daily_stats WHERE date=?", (_today(),)).fetchone()
    conn.close()
    if not row:
        return {"date": _today(), "realized_pnl": 0.0, "trade_count": 0,"concurrent_max": 0, "hard_stop_triggered": 0}
    return dict(row)

def add_realized_pnl(amount):
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO daily_stats (date) VALUES (?)", (_today(),))
        cur.execute(
            "UPDATE daily_stats SET realized_pnl = realized_pnl + ? WHERE date=?",
            (amount, _today()),
        )
        conn.commit()
        conn.close()

def increment_trade_count():
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO daily_stats (date) VALUES (?)", (_today(),))
        cur.execute(
            "UPDATE daily_stats SET trade_count = trade_count + 1 WHERE date=?", (_today(),)
        )
        conn.commit()
        conn.close()

def set_hard_stop_triggered():
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO daily_stats (date) VALUES (?)", (_today(),))
        cur.execute(
            "UPDATE daily_stats SET hard_stop_triggered = 1 WHERE date=?", (_today(),)
        )
        conn.commit()
        conn.close()

def update_concurrent_max(current_count):
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO daily_stats (date) VALUES (?)", (_today(),))
        cur.execute(
            "UPDATE daily_stats SET concurrent_max = MAX(concurrent_max, ?) WHERE date=?",
            (current_count, _today()),
        )
        conn.commit()
        conn.close()

def get_loss_streak():
    conn = get_conn()
    row = conn.execute("SELECT * FROM loss_streak WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {"consecutive_losses": 0, "locked_until": None}

def register_trade_result(is_loss: bool):
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        if is_loss:
            cur.execute(
                "UPDATE loss_streak SET consecutive_losses = consecutive_losses + 1, last_loss_at=? WHERE id=1",
                (datetime.utcnow().isoformat(),),
            )
        else:
            cur.execute("UPDATE loss_streak SET consecutive_losses = 0 WHERE id=1")
        conn.commit()
        conn.close()

def set_loss_streak_lock(until_iso):
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE loss_streak SET locked_until=? WHERE id=1", (until_iso,))
        conn.commit()
        conn.close()

def get_cooldown_until():
    conn = get_conn()
    row = conn.execute("SELECT locked_until FROM cooldown_state WHERE id=1").fetchone()
    conn.close()
    return row["locked_until"] if row else None

def set_cooldown_until(until_iso):
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE cooldown_state SET locked_until=? WHERE id=1", (until_iso,))
        conn.commit()
        conn.close()

def update_heartbeat():
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE heartbeat SET last_beat=? WHERE id=1", (datetime.utcnow().isoformat(),))
        conn.commit()
        conn.close()

def get_last_heartbeat():
    conn = get_conn()
    row = conn.execute("SELECT last_beat FROM heartbeat WHERE id=1").fetchone()
    conn.close()
    return row["last_beat"] if row else None

def upsert_position(symbol, side, entry_price, avg_entry_price, quantity, leverage,
                     sl_price=None, sl_order_id=None, is_dca=False):
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        existing = cur.execute(
            "SELECT id FROM positions_tracked WHERE symbol=? AND status='open'", (symbol,)
        ).fetchone()
        if existing:
            cur.execute(
                """UPDATE positions_tracked SET side=?, entry_price=?, avg_entry_price=?,
                   quantity=?, leverage=?, sl_price=?, sl_order_id=?, is_dca=?
                   WHERE id=?""",
                (side, entry_price, avg_entry_price, quantity, leverage,
                 sl_price, sl_order_id, int(is_dca), existing["id"]),
            )
        else:
            cur.execute(
                """INSERT INTO positions_tracked
                   (symbol, side, entry_price, avg_entry_price, quantity, leverage,
                    sl_price, sl_order_id, opened_at, status, is_dca)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (symbol, side, entry_price, avg_entry_price, quantity, leverage,
                 sl_price, sl_order_id, datetime.utcnow().isoformat(), 'open', int(is_dca)),
            )
        conn.commit()
        conn.close()

def close_position(symbol, realized_pnl=0.0):
    with _lock:
        conn = get_conn()
        conn.execute(
            """UPDATE positions_tracked SET status='closed', closed_at=?, realized_pnl=?
               WHERE symbol=? AND status='open'""",
            (datetime.utcnow().isoformat(), realized_pnl, symbol),
        )
        conn.commit()
        conn.close()

def get_open_positions():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM positions_tracked WHERE status='open'").fetchall()
    conn.close()
    return [dict(r) for r in rows]
