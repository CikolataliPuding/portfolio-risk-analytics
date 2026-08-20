"""Cache freshness: the single place that decides whether stored data is
recent enough to skip a network call.

prices.py and macro.py must call should_refresh_prices / should_refresh_macro
instead of comparing dates themselves, so the TTL rule (config.CACHE_TTL_DAYS)
is enforced in exactly one place.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from finrisk.config import CACHE_TTL_DAYS
from finrisk.db import repository as repo


def now_iso() -> str:
    """Current UTC timestamp in the same format written to fetched_at."""
    return datetime.now(timezone.utc).isoformat()


def _parse_fetched_at(fetched_at: str) -> datetime:
    dt = datetime.fromisoformat(fetched_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_stale(
    fetched_at: str | None,
    ttl_days: int = CACHE_TTL_DAYS,
    now: datetime | None = None,
) -> bool:
    """True if `fetched_at` is missing or older than `ttl_days`."""
    if fetched_at is None:
        return True
    reference = now if now is not None else datetime.now(timezone.utc)
    age = reference - _parse_fetched_at(fetched_at)
    return age.total_seconds() > ttl_days * 86400


def should_refresh_prices(
    conn: sqlite3.Connection, ticker: str, now: datetime | None = None
) -> bool:
    """True if `ticker`'s stored prices are missing or past the cache TTL."""
    return is_stale(repo.get_latest_price_fetch(conn, ticker), now=now)


def should_refresh_macro(
    conn: sqlite3.Connection, series_id: str, now: datetime | None = None
) -> bool:
    """True if `series_id`'s stored observations are missing or past the cache TTL."""
    return is_stale(repo.get_latest_macro_fetch(conn, series_id), now=now)
