"""SQLite connection management.

Every connection has foreign keys enabled and the schema applied (schema.sql
uses CREATE TABLE/INDEX IF NOT EXISTS, so applying it on every connect is
cheap and idempotent).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from finrisk.config import DB_PATH, SCHEMA_PATH


def _apply_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text(encoding="utf-8"))


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _apply_schema(conn, SCHEMA_PATH)
    return conn


@contextmanager
def get_connection(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with foreign keys on and schema applied.

    Commits on clean exit, rolls back on exception, always closes.
    """
    conn = _connect(Path(db_path) if db_path is not None else DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
