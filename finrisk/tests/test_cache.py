"""Tests for ingest/cache.py: freshness rules used to skip network calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from finrisk.db.connection import get_connection
from finrisk.db import repository as repo
from finrisk.ingest import cache


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


def test_is_stale_when_never_fetched():
    assert cache.is_stale(None) is True


def test_is_stale_false_within_ttl():
    now = datetime.now(timezone.utc)
    fetched_at = (now - timedelta(days=1)).isoformat()
    assert cache.is_stale(fetched_at, ttl_days=7, now=now) is False


def test_is_stale_true_past_ttl():
    now = datetime.now(timezone.utc)
    fetched_at = (now - timedelta(days=8)).isoformat()
    assert cache.is_stale(fetched_at, ttl_days=7, now=now) is True


def test_should_refresh_prices_false_when_recent(db_path):
    now = datetime.now(timezone.utc)
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
            fetched_at=(now - timedelta(days=1)).isoformat(),
        )
        assert cache.should_refresh_prices(conn, "SPY", now=now) is False


def test_should_refresh_prices_true_when_stale(db_path):
    now = datetime.now(timezone.utc)
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
            fetched_at=(now - timedelta(days=10)).isoformat(),
        )
        assert cache.should_refresh_prices(conn, "SPY", now=now) is True


def test_should_refresh_prices_true_when_never_fetched(db_path):
    with get_connection(db_path) as conn:
        assert cache.should_refresh_prices(conn, "SPY") is True


def test_should_refresh_macro_respects_ttl(db_path):
    now = datetime.now(timezone.utc)
    with get_connection(db_path) as conn:
        repo.upsert_macro_observation(
            conn, "DGS10", "2024-01-05", 4.05, (now - timedelta(days=2)).isoformat()
        )
        assert cache.should_refresh_macro(conn, "DGS10", now=now) is False

        repo.upsert_macro_observation(
            conn, "DGS10", "2024-01-05", 4.05, (now - timedelta(days=9)).isoformat()
        )
        assert cache.should_refresh_macro(conn, "DGS10", now=now) is True
