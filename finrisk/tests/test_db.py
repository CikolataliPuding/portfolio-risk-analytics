"""Tests for the db layer: schema creation, connection, and repository upserts."""

from __future__ import annotations

import sqlite3

import pytest

from finrisk.db.connection import get_connection
from finrisk.db import repository as repo


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


def test_schema_creates_cleanly(db_path):
    with get_connection(db_path) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"instruments", "prices_weekly", "macro_series", "data_gaps"} <= tables


def test_foreign_keys_enabled(db_path):
    with get_connection(db_path) as conn:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_reopening_connection_does_not_fail(db_path):
    with get_connection(db_path):
        pass
    # Reapplying schema.sql (IF NOT EXISTS) on a second connect must not error.
    with get_connection(db_path) as conn:
        conn.execute("SELECT 1")


def test_upsert_instrument_does_not_duplicate(db_path):
    with get_connection(db_path) as conn:
        repo.upsert_instrument(conn, "SPY", name="SPDR S&P 500", asset_class="equity")
        repo.upsert_instrument(conn, "SPY", name="SPDR S&P 500 ETF Trust", asset_class="equity")
        rows = conn.execute("SELECT * FROM instruments WHERE ticker = 'SPY'").fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "SPDR S&P 500 ETF Trust"


def test_upsert_price_week_does_not_duplicate(db_path):
    with get_connection(db_path) as conn:
        repo.upsert_instrument(conn, "SPY")
        repo.upsert_price_week(
            conn,
            ticker="SPY",
            week_end="2024-01-05",
            adj_close=470.0,
            close=471.0,
            volume=1000.0,
            source="yfinance",
            fetched_at="2024-01-06T00:00:00",
        )
        repo.upsert_price_week(
            conn,
            ticker="SPY",
            week_end="2024-01-05",
            adj_close=470.5,
            close=471.5,
            volume=1100.0,
            source="yfinance",
            fetched_at="2024-01-07T00:00:00",
        )
        rows = conn.execute(
            "SELECT * FROM prices_weekly WHERE ticker = 'SPY' AND week_end = '2024-01-05'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["adj_close"] == 470.5
    assert rows[0]["fetched_at"] == "2024-01-07T00:00:00"


def test_upsert_macro_observation_does_not_duplicate(db_path):
    with get_connection(db_path) as conn:
        repo.upsert_macro_observation(
            conn, "DGS10", "2024-01-05", 4.05, "2024-01-06T00:00:00"
        )
        repo.upsert_macro_observation(
            conn, "DGS10", "2024-01-05", 4.10, "2024-01-07T00:00:00"
        )
        rows = repo.get_macro_series(conn, "DGS10")
    assert len(rows) == 1
    assert rows[0]["value"] == 4.10


def test_price_gap_leaves_null_and_records_gap(db_path):
    with get_connection(db_path) as conn:
        repo.upsert_instrument(conn, "SPY")
        repo.upsert_price_week(
            conn,
            ticker="SPY",
            week_end="2024-01-12",
            adj_close=None,
            close=None,
            volume=None,
            source="yfinance",
            fetched_at="2024-01-13T00:00:00",
        )
        repo.insert_data_gap(
            conn,
            ticker="SPY",
            expected_date="2024-01-12",
            reason="no data returned by source",
            detected_at="2024-01-13T00:00:00",
        )
        price_row = conn.execute(
            "SELECT * FROM prices_weekly WHERE ticker = 'SPY' AND week_end = '2024-01-12'"
        ).fetchone()
        gaps = repo.get_data_gaps(conn, ticker="SPY")

    assert price_row["adj_close"] is None
    assert len(gaps) == 1
    assert gaps[0]["reason"] == "no data returned by source"


def test_invalid_ticker_price_lookup_returns_empty(db_path):
    with get_connection(db_path) as conn:
        rows = repo.get_prices(conn, "NOT_A_REAL_TICKER")
    assert rows == []


def test_foreign_key_violation_is_rejected(db_path):
    with get_connection(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            repo.upsert_price_week(
                conn,
                ticker="NOPE",
                week_end="2024-01-05",
                adj_close=1.0,
                close=1.0,
                volume=1.0,
                source="yfinance",
                fetched_at="2024-01-06T00:00:00",
            )
