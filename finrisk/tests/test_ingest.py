"""Tests for ingest/prices.py and ingest/macro.py. All network calls (yfinance,
requests) are mocked -- no real network access."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import requests as requests_module

from finrisk.db.connection import get_connection
from finrisk.db import repository as repo
from finrisk.ingest import macro, prices


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


def _daily_frame(rows: dict[str, dict]) -> pd.DataFrame:
    """Build a yfinance-shaped daily DataFrame from {date_str: {Close, Adj Close, Volume}}."""
    index = pd.to_datetime(list(rows.keys()))
    df = pd.DataFrame(list(rows.values()), index=index)
    return df


def test_fetch_weekly_prices_stores_adj_close_under_friday(db_path, monkeypatch):
    # Week of 2024-01-01 (Mon) .. 2024-01-05 (Fri): full trading week.
    frame = _daily_frame(
        {
            "2024-01-02": {"Close": 100.0, "Adj Close": 99.0, "Volume": 1000},
            "2024-01-03": {"Close": 101.0, "Adj Close": 100.0, "Volume": 1100},
            "2024-01-05": {"Close": 102.0, "Adj Close": 101.0, "Volume": 1200},
        }
    )
    monkeypatch.setattr(prices.yf, "download", lambda *a, **k: frame)

    with get_connection(db_path) as conn:
        prices.fetch_weekly_prices(conn, ["SPY"], "2024-01-01", "2024-01-05")
        row = conn.execute(
            "SELECT * FROM prices_weekly WHERE ticker = 'SPY' AND week_end = '2024-01-05'"
        ).fetchone()

    assert row is not None
    assert row["adj_close"] == 101.0
    assert row["close"] == 102.0
    assert row["source"] == "yfinance"


def test_holiday_friday_uses_last_trading_day_of_week(db_path, monkeypatch):
    # Friday 2024-01-05 is a "holiday": last trading day is Thursday 2024-01-04.
    frame = _daily_frame(
        {
            "2024-01-02": {"Close": 100.0, "Adj Close": 99.0, "Volume": 1000},
            "2024-01-04": {"Close": 103.0, "Adj Close": 102.0, "Volume": 1300},
        }
    )
    monkeypatch.setattr(prices.yf, "download", lambda *a, **k: frame)

    with get_connection(db_path) as conn:
        prices.fetch_weekly_prices(conn, ["SPY"], "2024-01-01", "2024-01-05")
        row = conn.execute(
            "SELECT * FROM prices_weekly WHERE ticker = 'SPY' AND week_end = '2024-01-05'"
        ).fetchone()

    assert row is not None
    assert row["adj_close"] == 102.0  # Thursday's close, written under the Friday date


def test_missing_week_creates_null_row_and_gap(db_path, monkeypatch):
    # Two-week range, but yfinance only has data for the first week.
    frame = _daily_frame(
        {
            "2024-01-02": {"Close": 100.0, "Adj Close": 99.0, "Volume": 1000},
        }
    )
    monkeypatch.setattr(prices.yf, "download", lambda *a, **k: frame)

    with get_connection(db_path) as conn:
        prices.fetch_weekly_prices(conn, ["SPY"], "2024-01-01", "2024-01-12")
        second_week = conn.execute(
            "SELECT * FROM prices_weekly WHERE ticker = 'SPY' AND week_end = '2024-01-12'"
        ).fetchone()
        gaps = repo.get_data_gaps(conn, ticker="SPY")

    assert second_week is not None
    assert second_week["adj_close"] is None
    assert second_week["close"] is None
    assert any(g["expected_date"] == "2024-01-12" for g in gaps)


def test_invalid_ticker_returns_empty_frame_without_crashing(db_path, monkeypatch):
    monkeypatch.setattr(prices.yf, "download", lambda *a, **k: pd.DataFrame())

    with get_connection(db_path) as conn:
        prices.fetch_weekly_prices(conn, ["NOT_A_REAL_TICKER"], "2024-01-01", "2024-01-05")
        row = conn.execute(
            "SELECT * FROM prices_weekly WHERE ticker = 'NOT_A_REAL_TICKER' AND week_end = '2024-01-05'"
        ).fetchone()
        gaps = repo.get_data_gaps(conn, ticker="NOT_A_REAL_TICKER")

    assert row is not None
    assert row["adj_close"] is None
    assert len(gaps) == 1


def test_network_failure_retries_then_records_gap_without_raising(db_path, monkeypatch):
    calls = {"count": 0}

    def _always_fails(*a, **k):
        calls["count"] += 1
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(prices.yf, "download", _always_fails)
    monkeypatch.setattr(prices.time, "sleep", lambda _seconds: None)

    with get_connection(db_path) as conn:
        prices.fetch_weekly_prices(conn, ["SPY"], "2024-01-01", "2024-01-05")
        gaps = repo.get_data_gaps(conn, ticker="SPY")

    assert calls["count"] == prices.MAX_RETRIES
    assert len(gaps) == 1
    assert "fetch failed" in gaps[0]["reason"]


def test_fresh_cache_skips_network_call(db_path, monkeypatch):
    calls = {"count": 0}
    monkeypatch.setattr(
        prices.yf, "download", lambda *a, **k: calls.__setitem__("count", calls["count"] + 1)
    )

    now = datetime.now(timezone.utc)
    with get_connection(db_path) as conn:
        repo.upsert_instrument(conn, "SPY")
        repo.upsert_price_week(
            conn,
            ticker="SPY",
            week_end="2024-01-05",
            adj_close=101.0,
            close=102.0,
            volume=1200.0,
            source="yfinance",
            fetched_at=(now - timedelta(days=1)).isoformat(),
        )
        prices.fetch_weekly_prices(conn, ["SPY"], "2024-01-01", "2024-01-05")

    assert calls["count"] == 0


# --- ingest/macro.py ---------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict, status_ok: bool = True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise requests_module.HTTPError("bad status")

    def json(self):
        return self._payload


def test_fetch_macro_series_stores_values(db_path, monkeypatch):
    monkeypatch.setattr(macro.config, "FRED_API_KEY", "test-key")
    payload = {
        "observations": [
            {"date": "2024-01-05", "value": "4.05"},
            {"date": "2024-01-12", "value": "4.10"},
        ]
    }
    monkeypatch.setattr(macro.requests, "get", lambda *a, **k: _FakeResponse(payload))

    with get_connection(db_path) as conn:
        macro.fetch_macro_series(conn, ["DGS10"], "2024-01-01", "2024-01-12")
        rows = repo.get_macro_series(conn, "DGS10")

    assert len(rows) == 2
    assert rows[0]["value"] == 4.05
    assert rows[1]["value"] == 4.10


def test_missing_mid_series_value_creates_gap(db_path, monkeypatch):
    monkeypatch.setattr(macro.config, "FRED_API_KEY", "test-key")
    payload = {
        "observations": [
            {"date": "2024-01-05", "value": "."},
            {"date": "2024-01-12", "value": "4.10"},
        ]
    }
    monkeypatch.setattr(macro.requests, "get", lambda *a, **k: _FakeResponse(payload))

    with get_connection(db_path) as conn:
        macro.fetch_macro_series(conn, ["DGS10"], "2024-01-01", "2024-01-12")
        rows = repo.get_macro_series(conn, "DGS10")
        gaps = repo.get_data_gaps(conn, ticker="DGS10")

    assert rows[0]["value"] is None
    assert len(gaps) == 1
    assert gaps[0]["reason"] == "missing value returned by FRED"


def test_latest_observation_missing_uses_publication_lag_reason(db_path, monkeypatch):
    monkeypatch.setattr(macro.config, "FRED_API_KEY", "test-key")
    payload = {
        "observations": [
            {"date": "2024-01-05", "value": "4.05"},
            {"date": "2024-01-12", "value": "."},
        ]
    }
    monkeypatch.setattr(macro.requests, "get", lambda *a, **k: _FakeResponse(payload))

    with get_connection(db_path) as conn:
        macro.fetch_macro_series(conn, ["DGS10"], "2024-01-01", "2024-01-12")
        gaps = repo.get_data_gaps(conn, ticker="DGS10")

    assert len(gaps) == 1
    assert gaps[0]["expected_date"] == "2024-01-12"
    assert "publication lag" in gaps[0]["reason"]


def test_missing_api_key_records_gap_without_crashing(db_path, monkeypatch):
    monkeypatch.setattr(macro.config, "FRED_API_KEY", None)
    calls = {"count": 0}
    monkeypatch.setattr(
        macro.requests, "get", lambda *a, **k: calls.__setitem__("count", calls["count"] + 1)
    )

    with get_connection(db_path) as conn:
        macro.fetch_macro_series(conn, ["DGS10"], "2024-01-01", "2024-01-12")
        gaps = repo.get_data_gaps(conn, ticker="DGS10")

    assert calls["count"] == 0
    assert len(gaps) == 1
    assert gaps[0]["reason"] == "FRED_API_KEY not set"


def test_macro_network_failure_retries_then_records_gap(db_path, monkeypatch):
    monkeypatch.setattr(macro.config, "FRED_API_KEY", "test-key")
    calls = {"count": 0}

    def _always_fails(*a, **k):
        calls["count"] += 1
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(macro.requests, "get", _always_fails)
    monkeypatch.setattr(macro.time, "sleep", lambda _seconds: None)

    with get_connection(db_path) as conn:
        macro.fetch_macro_series(conn, ["DGS10"], "2024-01-01", "2024-01-12")
        gaps = repo.get_data_gaps(conn, ticker="DGS10")

    assert calls["count"] == macro.config.MAX_RETRIES
    assert len(gaps) == 1
    assert "fetch failed" in gaps[0]["reason"]


def test_macro_fresh_cache_skips_network_call(db_path, monkeypatch):
    monkeypatch.setattr(macro.config, "FRED_API_KEY", "test-key")
    calls = {"count": 0}
    monkeypatch.setattr(
        macro.requests, "get", lambda *a, **k: calls.__setitem__("count", calls["count"] + 1)
    )

    now = datetime.now(timezone.utc)
    with get_connection(db_path) as conn:
        repo.upsert_macro_observation(
            conn, "DGS10", "2024-01-05", 4.05, (now - timedelta(days=1)).isoformat()
        )
        macro.fetch_macro_series(conn, ["DGS10"], "2024-01-01", "2024-01-12")

    assert calls["count"] == 0
