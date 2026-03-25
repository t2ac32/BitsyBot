"""
SQLite database interface — schema creation and CRUD helpers.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

DB_PATH = Path(__file__).parent.parent / "bitsybot.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if they don't exist."""
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS grids (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                book        TEXT    NOT NULL,
                lower_price REAL    NOT NULL,
                upper_price REAL    NOT NULL,
                levels      INTEGER NOT NULL,
                investment  REAL    NOT NULL,
                mode        TEXT    NOT NULL DEFAULT 'paper',
                status      TEXT    NOT NULL DEFAULT 'active',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS grid_levels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                grid_id     INTEGER NOT NULL REFERENCES grids(id),
                level_index INTEGER NOT NULL,
                price       REAL    NOT NULL,
                side        TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'open',
                order_id    TEXT,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                grid_id     INTEGER REFERENCES grids(id),
                level_index INTEGER,
                side        TEXT    NOT NULL,
                price       REAL    NOT NULL,
                amount      REAL    NOT NULL,
                pnl         REAL    DEFAULT 0,
                fees        REAL    DEFAULT 0,
                mode        TEXT    NOT NULL DEFAULT 'paper',
                timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS balance_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                mode        TEXT    NOT NULL,
                balance_mxn REAL    NOT NULL,
                balance_btc REAL    NOT NULL DEFAULT 0,
                timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS candles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                book        TEXT    NOT NULL,
                time_bucket TEXT    NOT NULL,
                ts          TIMESTAMP NOT NULL,
                open        REAL    NOT NULL,
                high        REAL    NOT NULL,
                low         REAL    NOT NULL,
                close       REAL    NOT NULL,
                volume      REAL    NOT NULL,
                UNIQUE(book, time_bucket, ts)
            );

            CREATE INDEX IF NOT EXISTS idx_trades_mode ON trades(mode);
            CREATE INDEX IF NOT EXISTS idx_trades_grid ON trades(grid_id);
            CREATE INDEX IF NOT EXISTS idx_balance_mode ON balance_history(mode);
            CREATE INDEX IF NOT EXISTS idx_candles_book_bucket ON candles(book, time_bucket, ts);
        """)


# ── Grid helpers ──────────────────────────────────────────────────────────────

def insert_grid(book: str, lower: float, upper: float, levels: int,
                investment: float, mode: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO grids (book, lower_price, upper_price, levels, investment, mode) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (book, lower, upper, levels, investment, mode),
        )
        return cur.lastrowid


def update_grid_status(grid_id: int, status: str) -> None:
    with db() as conn:
        conn.execute("UPDATE grids SET status=? WHERE id=?", (status, grid_id))


def insert_grid_level(grid_id: int, index: int, price: float, side: str,
                      order_id: str = None) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO grid_levels (grid_id, level_index, price, side, order_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (grid_id, index, price, side, order_id),
        )
        return cur.lastrowid


def update_level_status(grid_id: int, level_index: int, status: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE grid_levels SET status=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE grid_id=? AND level_index=?",
            (status, grid_id, level_index),
        )


def get_active_levels(grid_id: int) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM grid_levels WHERE grid_id=? AND status='open' ORDER BY price",
            (grid_id,),
        ).fetchall()


# ── Trade helpers ─────────────────────────────────────────────────────────────

def insert_trade(grid_id: int, level_index: int, side: str, price: float,
                 amount: float, pnl: float, fees: float, mode: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO trades (grid_id, level_index, side, price, amount, pnl, fees, mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (grid_id, level_index, side, price, amount, pnl, fees, mode),
        )
        return cur.lastrowid


def get_trades(mode: str = None, grid_id: int = None, limit: int = 500) -> list[sqlite3.Row]:
    clauses, params = [], []
    if mode:
        clauses.append("mode=?")
        params.append(mode)
    if grid_id is not None:
        clauses.append("grid_id=?")
        params.append(grid_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with db() as conn:
        return conn.execute(
            f"SELECT * FROM trades {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()


# ── Balance history helpers ───────────────────────────────────────────────────

def record_balance(mode: str, balance_mxn: float, balance_btc: float = 0) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO balance_history (mode, balance_mxn, balance_btc) VALUES (?, ?, ?)",
            (mode, balance_mxn, balance_btc),
        )


def get_balance_history(mode: str, limit: int = 10000) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM balance_history WHERE mode=? ORDER BY timestamp",
            (mode,),
        ).fetchall()


# ── Candle helpers ────────────────────────────────────────────────────────────

def upsert_candles(book: str, time_bucket: str, candles: list) -> int:
    """Insert candles, ignore duplicates. Returns number inserted."""
    inserted = 0
    with db() as conn:
        for c in candles:
            cur = conn.execute(
                "INSERT OR IGNORE INTO candles (book, time_bucket, ts, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (book, time_bucket, c.timestamp, c.open, c.high, c.low, c.close, c.volume),
            )
            inserted += cur.rowcount
    return inserted


def get_candles(book: str, time_bucket: str,
                start: datetime = None, end: datetime = None) -> list[sqlite3.Row]:
    clauses = ["book=?", "time_bucket=?"]
    params: list = [book, time_bucket]
    if start:
        clauses.append("ts >= ?")
        params.append(start)
    if end:
        clauses.append("ts <= ?")
        params.append(end)
    with db() as conn:
        return conn.execute(
            "SELECT * FROM candles WHERE " + " AND ".join(clauses) + " ORDER BY ts",
            params,
        ).fetchall()
