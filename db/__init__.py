import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "terminalmd.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> sqlite3.Connection:
    conn = get_conn()
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn
