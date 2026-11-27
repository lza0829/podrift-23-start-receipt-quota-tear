from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH


def connect() -> sqlite3.Connection:
    # autocommit at the driver boundary; every request transaction explicitly
    # issues BEGIN/BEGIN IMMEDIATE so SQLite lock timing is deterministic.
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30.0,
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    synchronous = os.environ.get("PODRIFT_SQLITE_SYNCHRONOUS", "NORMAL").upper()
    if synchronous in {"OFF", "NORMAL", "FULL", "EXTRA"}:
        conn.execute(f"PRAGMA synchronous = {synchronous}")
    return conn


@contextmanager
def db():
    conn = connect()
    conn.execute("BEGIN")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def write_db():
    """Serialize the read-check-write quota/start decision.

    BEGIN IMMEDIATE takes the SQLite write lock before reading quotas. A quota
    update and a start can therefore not interleave their checks and writes.
    """
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS images (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          tag TEXT NOT NULL,
          pull_state TEXT NOT NULL,
          layers_done INTEGER NOT NULL DEFAULT 0,
          layers_total INTEGER NOT NULL DEFAULT 4,
          ready_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pods (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          image_id INTEGER,
          run_state TEXT NOT NULL,
          sidecar TEXT,
          cpu_quota REAL NOT NULL DEFAULT 1.0,
          mem_quota_mb INTEGER NOT NULL DEFAULT 512,
          FOREIGN KEY(image_id) REFERENCES images(id)
        );
        CREATE TABLE IF NOT EXISTS start_orders (
          id INTEGER PRIMARY KEY,
          pod_id INTEGER NOT NULL,
          action TEXT NOT NULL,
          target_image_id INTEGER,
          status TEXT NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(pod_id) REFERENCES pods(id)
        );
        CREATE TABLE IF NOT EXISTS probes (
          id INTEGER PRIMARY KEY,
          pod_id INTEGER NOT NULL UNIQUE,
          color TEXT NOT NULL,
          fail_streak INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT,
          FOREIGN KEY(pod_id) REFERENCES pods(id)
        );
        CREATE TABLE IF NOT EXISTS quotas (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          cpu_used REAL NOT NULL,
          cpu_limit REAL NOT NULL,
          mem_used_mb INTEGER NOT NULL,
          mem_limit_mb INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS restart_policy (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          mode TEXT NOT NULL,
          backoff_sec INTEGER NOT NULL DEFAULT 5
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY,
          kind TEXT NOT NULL,
          ref TEXT,
          detail TEXT NOT NULL,
          at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quota_ledger (
          id INTEGER PRIMARY KEY,
          seq INTEGER NOT NULL UNIQUE,
          order_id INTEGER,
          kind TEXT NOT NULL,
          pod_id INTEGER,
          cpu_delta REAL NOT NULL DEFAULT 0,
          mem_delta_mb INTEGER NOT NULL DEFAULT 0,
          cpu_before REAL NOT NULL,
          cpu_after REAL NOT NULL,
          mem_before_mb INTEGER NOT NULL,
          mem_after_mb INTEGER NOT NULL,
          decision TEXT NOT NULL,
          top_hit INTEGER NOT NULL DEFAULT 0,
          detail TEXT,
          at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES start_orders(id),
          FOREIGN KEY(pod_id) REFERENCES pods(id)
        );
        CREATE INDEX IF NOT EXISTS idx_quota_ledger_order ON quota_ledger(order_id);
        CREATE INDEX IF NOT EXISTS idx_quota_ledger_decision ON quota_ledger(decision, top_hit);
        """
    )

    order_cols = _column_names(conn, "start_orders")
    order_additions = {
        "quota_decision": "TEXT",
        "quota_top_hit": "INTEGER NOT NULL DEFAULT 0",
        "quota_snapshot": "TEXT",
    }
    for name, ddl in order_additions.items():
        if name not in order_cols:
            conn.execute(f"ALTER TABLE start_orders ADD COLUMN {name} {ddl}")

    quota_cols = _column_names(conn, "quotas")
    if "version" not in quota_cols:
        conn.execute("ALTER TABLE quotas ADD COLUMN version INTEGER NOT NULL DEFAULT 0")

    # WAL makes the linearized writer behavior explicit and lets read screens
    # query while a write transaction is in progress.
    if DB_PATH.name != ":memory:":
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
