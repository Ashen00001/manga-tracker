import json
import sqlite3
import threading
import time
from datetime import datetime, timezone

from .config import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,              -- "volume:<isbn>" or "series:<series>|<edition>"
    kind TEXT NOT NULL CHECK (kind IN ('volume', 'series')),
    isbn TEXT,
    series TEXT,
    edition TEXT,
    title TEXT NOT NULL,
    cover_url TEXT,
    book_json TEXT,
    created_at TEXT NOT NULL,
    last_checked_at TEXT
);
CREATE TABLE IF NOT EXISTS stock_state (
    isbn TEXT NOT NULL,
    source TEXT NOT NULL,
    store_id TEXT NOT NULL,
    store_name TEXT,
    status TEXT NOT NULL,
    quantity INTEGER,
    price REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (isbn, source, store_id)
);
CREATE TABLE IF NOT EXISTS known_volumes (
    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    isbn TEXT NOT NULL,
    PRIMARY KEY (watch_id, isbn)
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    isbn TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    connection = getattr(_local, "conn", None)
    if connection is None:
        connection = sqlite3.connect(settings.data_dir / "tracker.db", timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(SCHEMA)
        _local.conn = connection
    return connection


# --- cache ---------------------------------------------------------------

def cache_get(key: str):
    row = conn().execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,)).fetchone()
    if row is None or row["expires_at"] < time.time():
        return None
    return json.loads(row["value"])


def cache_set(key: str, value, ttl_hours: float) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time() + ttl_hours * 3600),
        )


# --- watches -------------------------------------------------------------

def watch_key(kind: str, isbn: str | None = None, series: str | None = None, edition: str | None = None) -> str:
    return f"volume:{isbn}" if kind == "volume" else f"series:{(series or '').lower()}|{(edition or '').lower()}"


def add_watch(kind: str, title: str, *, isbn=None, series=None, edition=None, cover_url=None, book: dict | None = None) -> dict:
    key = watch_key(kind, isbn, series, edition)
    with conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO watches (key, kind, isbn, series, edition, title, cover_url, book_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, kind, isbn, series, edition, title, cover_url, json.dumps(book) if book else None, now_iso()),
        )
    return dict(conn().execute("SELECT * FROM watches WHERE key = ?", (key,)).fetchone())


def list_watches(kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM watches" + (" WHERE kind = ?" if kind else "") + " ORDER BY created_at DESC"
    return [dict(r) for r in conn().execute(sql, (kind,) if kind else ())]


def get_watch(watch_id: int) -> dict | None:
    row = conn().execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    return dict(row) if row else None


def delete_watch(watch_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM watches WHERE id = ?", (watch_id,))


def touch_watch(watch_id: int) -> None:
    with conn() as c:
        c.execute("UPDATE watches SET last_checked_at = ? WHERE id = ?", (now_iso(), watch_id))


# --- stock state (for restock detection) ---------------------------------

def stock_state(isbn: str) -> dict[tuple[str, str], str]:
    rows = conn().execute("SELECT source, store_id, status FROM stock_state WHERE isbn = ?", (isbn,))
    return {(r["source"], r["store_id"]): r["status"] for r in rows}


def save_stock_state(isbn: str, stores: list) -> None:
    stamp = now_iso()
    with conn() as c:
        c.executemany(
            """INSERT OR REPLACE INTO stock_state (isbn, source, store_id, store_name, status, quantity, price, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(isbn, s.source, s.store_id, s.store_name, s.status, s.quantity, s.price, stamp) for s in stores],
        )


# --- series watches ------------------------------------------------------

def known_volumes(watch_id: int) -> set[str]:
    return {r["isbn"] for r in conn().execute("SELECT isbn FROM known_volumes WHERE watch_id = ?", (watch_id,))}


def add_known_volumes(watch_id: int, isbns: list[str]) -> None:
    with conn() as c:
        c.executemany("INSERT OR IGNORE INTO known_volumes (watch_id, isbn) VALUES (?, ?)", [(watch_id, i) for i in isbns])


# --- activity log --------------------------------------------------------

def log_event(kind: str, message: str, isbn: str | None = None) -> None:
    with conn() as c:
        c.execute("INSERT INTO events (at, kind, message, isbn) VALUES (?, ?, ?, ?)", (now_iso(), kind, message, isbn))
        c.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 300)")


def recent_events(limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn().execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
