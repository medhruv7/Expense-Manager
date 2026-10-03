import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_messages (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,                 -- sms | email | manual
    sender TEXT,
    subject TEXT,
    body TEXT NOT NULL,
    received_at TEXT NOT NULL,            -- local time 'YYYY-MM-DD HH:MM:SS'
    external_id TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending', -- parsed | duplicate | ignored | unparsed
    note TEXT,
    transaction_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_messages(status);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    txn_time TEXT NOT NULL,
    amount REAL NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    merchant TEXT,
    payee_vpa TEXT,
    category TEXT NOT NULL DEFAULT 'Other',
    category_source TEXT,                 -- user | rule | hint | default | llm | auto | fallback
    bank TEXT,
    account_last4 TEXT,
    instrument TEXT,                      -- UPI | CREDIT_CARD | DEBIT_CARD | NETBANKING | ATM | OTHER
    ref_no TEXT,
    source TEXT NOT NULL,
    sources TEXT,
    parsed_by TEXT,                       -- regex | llm | manual
    excluded INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_txn_time ON transactions(txn_time);
CREATE INDEX IF NOT EXISTS idx_txn_amount ON transactions(amount);

CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL UNIQUE,         -- lowercase substring matched on merchant / VPA
    category TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_db_path: Path | None = None


def init(path: Path | str | None = None) -> None:
    global _db_path
    _db_path = Path(path) if path else config.DB_PATH
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    if _db_path is None:
        init()
    conn = sqlite3.connect(_db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, None if value is None else str(value)),
    )
