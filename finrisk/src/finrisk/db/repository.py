"""Read/write functions for every table.

All SQL lives here. Ingest modules call these functions instead of
executing SQL themselves. Every write is an upsert: calling the same
ingest run twice must not create duplicate rows.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Mapping, Sequence

# --- instruments -----------------------------------------------------------


def ensure_instrument(conn: sqlite3.Connection, ticker: str) -> None:
    """Insert a bare-minimum instruments row if one doesn't already exist.

    Used by ingest modules that only have a ticker (no name/asset_class/etc.)
    and need to satisfy the prices_weekly foreign key without clobbering any
    metadata already stored for that ticker.
    """
    conn.execute(
        "INSERT OR IGNORE INTO instruments (ticker) VALUES (?)", (ticker,)
    )


def upsert_instrument(
    conn: sqlite3.Connection,
    ticker: str,
    name: str | None = None,
    asset_class: str | None = None,
    region: str | None = None,
    currency: str = "USD",
    is_active: int = 1,
    last_updated: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO instruments (ticker, name, asset_class, region, currency, is_active, last_updated)
        VALUES (:ticker, :name, :asset_class, :region, :currency, :is_active, :last_updated)
        ON CONFLICT (ticker) DO UPDATE SET
            name = excluded.name,
            asset_class = excluded.asset_class,
            region = excluded.region,
            currency = excluded.currency,
            is_active = excluded.is_active,
            last_updated = excluded.last_updated
        """,
        {
            "ticker": ticker,
            "name": name,
            "asset_class": asset_class,
            "region": region,
            "currency": currency,
            "is_active": is_active,
            "last_updated": last_updated,
        },
    )


def get_instrument(conn: sqlite3.Connection, ticker: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM instruments WHERE ticker = ?", (ticker,)
    ).fetchone()


# --- prices_weekly -----------------------------------------------------------


def upsert_price_week(
    conn: sqlite3.Connection,
    ticker: str,
    week_end: str,
    adj_close: float | None,
    close: float | None,
    volume: float | None,
    source: str,
    fetched_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO prices_weekly (ticker, week_end, adj_close, close, volume, source, fetched_at)
        VALUES (:ticker, :week_end, :adj_close, :close, :volume, :source, :fetched_at)
        ON CONFLICT (ticker, week_end) DO UPDATE SET
            adj_close = excluded.adj_close,
            close = excluded.close,
            volume = excluded.volume,
            source = excluded.source,
            fetched_at = excluded.fetched_at
        """,
        {
            "ticker": ticker,
            "week_end": week_end,
            "adj_close": adj_close,
            "close": close,
            "volume": volume,
            "source": source,
            "fetched_at": fetched_at,
        },
    )


def upsert_prices_weekly(conn: sqlite3.Connection, rows: Iterable[Mapping]) -> None:
    """Upsert many prices_weekly rows. Each row is a mapping with the same
    keys as upsert_price_week's arguments."""
    for row in rows:
        upsert_price_week(
            conn,
            ticker=row["ticker"],
            week_end=row["week_end"],
            adj_close=row.get("adj_close"),
            close=row.get("close"),
            volume=row.get("volume"),
            source=row["source"],
            fetched_at=row["fetched_at"],
        )


def get_prices(
    conn: sqlite3.Connection,
    ticker: str,
    start: str | None = None,
    end: str | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM prices_weekly WHERE ticker = ?"
    params: list = [ticker]
    if start is not None:
        query += " AND week_end >= ?"
        params.append(start)
    if end is not None:
        query += " AND week_end <= ?"
        params.append(end)
    query += " ORDER BY week_end"
    return conn.execute(query, params).fetchall()


def get_latest_price_fetch(conn: sqlite3.Connection, ticker: str) -> str | None:
    """Most recent fetched_at across all weeks stored for this ticker.

    Used by ingest/cache.py to decide whether the cache is still fresh.
    """
    row = conn.execute(
        "SELECT MAX(fetched_at) AS fetched_at FROM prices_weekly WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    return row["fetched_at"] if row else None


# --- macro_series -----------------------------------------------------------


def upsert_macro_observation(
    conn: sqlite3.Connection,
    series_id: str,
    obs_date: str,
    value: float | None,
    fetched_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO macro_series (series_id, obs_date, value, fetched_at)
        VALUES (:series_id, :obs_date, :value, :fetched_at)
        ON CONFLICT (series_id, obs_date) DO UPDATE SET
            value = excluded.value,
            fetched_at = excluded.fetched_at
        """,
        {
            "series_id": series_id,
            "obs_date": obs_date,
            "value": value,
            "fetched_at": fetched_at,
        },
    )


def upsert_macro_series(conn: sqlite3.Connection, rows: Iterable[Mapping]) -> None:
    """Upsert many macro_series rows. Each row is a mapping with the same
    keys as upsert_macro_observation's arguments."""
    for row in rows:
        upsert_macro_observation(
            conn,
            series_id=row["series_id"],
            obs_date=row["obs_date"],
            value=row.get("value"),
            fetched_at=row["fetched_at"],
        )


def get_macro_series(
    conn: sqlite3.Connection,
    series_id: str,
    start: str | None = None,
    end: str | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM macro_series WHERE series_id = ?"
    params: list = [series_id]
    if start is not None:
        query += " AND obs_date >= ?"
        params.append(start)
    if end is not None:
        query += " AND obs_date <= ?"
        params.append(end)
    query += " ORDER BY obs_date"
    return conn.execute(query, params).fetchall()


def get_latest_macro_fetch(conn: sqlite3.Connection, series_id: str) -> str | None:
    """Most recent fetched_at across all observations stored for this series.

    Used by ingest/cache.py to decide whether the cache is still fresh.
    """
    row = conn.execute(
        "SELECT MAX(fetched_at) AS fetched_at FROM macro_series WHERE series_id = ?",
        (series_id,),
    ).fetchone()
    return row["fetched_at"] if row else None


# --- data_gaps -----------------------------------------------------------


def insert_data_gap(
    conn: sqlite3.Connection,
    ticker: str | None,
    expected_date: str | None,
    reason: str,
    detected_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO data_gaps (ticker, expected_date, reason, detected_at)
        VALUES (:ticker, :expected_date, :reason, :detected_at)
        """,
        {
            "ticker": ticker,
            "expected_date": expected_date,
            "reason": reason,
            "detected_at": detected_at,
        },
    )


def get_data_gaps(
    conn: sqlite3.Connection, ticker: str | None = None
) -> list[sqlite3.Row]:
    if ticker is None:
        return conn.execute(
            "SELECT * FROM data_gaps ORDER BY detected_at"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM data_gaps WHERE ticker = ? ORDER BY detected_at",
        (ticker,),
    ).fetchall()
