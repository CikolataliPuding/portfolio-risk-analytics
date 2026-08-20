"""Weekly ETF price ingestion via yfinance.

Fetches daily bars and resamples them to a Friday-anchored week: every
Friday in the requested range gets exactly one row, keyed to the last
trading day of that week (Monday-Sunday). If a whole week has no trading
data, the row is still written with NULL price fields and a matching
data_gaps entry is recorded -- missing data is never filled in.

Network failures are retried with exponential backoff (config.MAX_RETRIES,
config.BACKOFF_BASE_SECONDS). A ticker that keeps failing does not raise;
it is recorded in data_gaps and the rest of the batch continues.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Sequence

import pandas as pd
import yfinance as yf

from finrisk.config import BACKOFF_BASE_SECONDS, MAX_RETRIES
from finrisk.db import repository as repo
from finrisk.ingest import cache

SOURCE = "yfinance"


def _week_friday(d: date) -> date:
    """The Friday of the Monday-Sunday week containing `d`."""
    return d + timedelta(days=4 - d.weekday())


def _expected_fridays(start: str, end: str) -> list[date]:
    return [ts.date() for ts in pd.date_range(start=start, end=end, freq="W-FRI")]


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return float(value)


def _download_daily_with_retry(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Download daily bars, retrying on any exception with exponential backoff.

    Raises the last exception if every attempt fails; the caller turns that
    into a data_gaps entry instead of propagating it.
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return yf.download(
                ticker,
                start=start,
                end=end,
                interval="1d",
                progress=False,
                auto_adjust=False,
                threads=False,
            )
        except Exception as exc:  # network/library failures all retry the same way
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE_SECONDS * (2**attempt))
    raise RuntimeError(
        f"failed to download {ticker} after {MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc


def _resample_to_weekly_friday(daily: pd.DataFrame) -> dict[date, dict]:
    """Collapse daily bars to one row per Friday: the last trading day of
    each Monday-Sunday week, keyed by that week's Friday date."""
    if daily is None or daily.empty:
        return {}

    weekly: dict[date, dict] = {}
    for idx, row in daily.sort_index().iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        friday = _week_friday(d)
        weekly[friday] = {
            "adj_close": _safe_float(row.get("Adj Close")),
            "close": _safe_float(row.get("Close")),
            "volume": _safe_float(row.get("Volume")),
        }
    return weekly


def _fetch_and_store_ticker(
    conn, ticker: str, start: str, end: str, fetched_at: str
) -> None:
    try:
        daily = _download_daily_with_retry(ticker, start, end)
    except Exception as exc:
        repo.insert_data_gap(
            conn,
            ticker=ticker,
            expected_date=None,
            reason=f"fetch failed: {exc}",
            detected_at=fetched_at,
        )
        return

    weekly_by_friday = _resample_to_weekly_friday(daily)

    for friday in _expected_fridays(start, end):
        week_end = friday.isoformat()
        data = weekly_by_friday.get(friday)
        if data is None:
            repo.upsert_price_week(
                conn,
                ticker=ticker,
                week_end=week_end,
                adj_close=None,
                close=None,
                volume=None,
                source=SOURCE,
                fetched_at=fetched_at,
            )
            repo.insert_data_gap(
                conn,
                ticker=ticker,
                expected_date=week_end,
                reason="no trading data available for this week",
                detected_at=fetched_at,
            )
        else:
            repo.upsert_price_week(
                conn,
                ticker=ticker,
                week_end=week_end,
                adj_close=data["adj_close"],
                close=data["close"],
                volume=data["volume"],
                source=SOURCE,
                fetched_at=fetched_at,
            )


def fetch_weekly_prices(
    conn, tickers: Sequence[str], start: str, end: str, force: bool = False
) -> None:
    """Fetch and store weekly prices for each ticker in `tickers`.

    Tickers whose cache is still fresh (see ingest/cache.py) are skipped
    unless `force` is True. A failure on one ticker does not stop the batch.
    """
    for ticker in tickers:
        repo.ensure_instrument(conn, ticker)
        if not force and not cache.should_refresh_prices(conn, ticker):
            continue
        fetched_at = cache.now_iso()
        _fetch_and_store_ticker(conn, ticker, start, end, fetched_at)
