"""Macro series ingestion via the FRED API.

FRED marks a missing observation with the literal value "." -- that is
stored as NULL, never filled in. The most recent observation for a series
is often not yet published (e.g. monthly CPI released with a lag); when its
value is missing, that is recorded in data_gaps with a distinct reason so
it can be told apart from an actual data-quality gap.

data_gaps has no series_id column (it was designed around tickers), so for
macro series the `ticker` column is reused to hold the FRED series id.
"""

from __future__ import annotations

import time
from typing import Sequence

import requests

from finrisk import config
from finrisk.db import repository as repo
from finrisk.ingest import cache

SOURCE = "fred"


def _parse_value(raw: str | None) -> float | None:
    if raw is None or raw == ".":
        return None
    return float(raw)


def _download_observations_with_retry(
    series_id: str, start: str | None, end: str | None
) -> list[dict]:
    """Fetch observations for a series, retrying on any exception with
    exponential backoff. Raises the last exception if every attempt fails."""
    params = {
        "series_id": series_id,
        "api_key": config.FRED_API_KEY,
        "file_type": "json",
    }
    if start is not None:
        params["observation_start"] = start
    if end is not None:
        params["observation_end"] = end

    last_exc: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        try:
            response = requests.get(config.FRED_API_BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            return response.json().get("observations", [])
        except Exception as exc:
            last_exc = exc
            if attempt < config.MAX_RETRIES - 1:
                time.sleep(config.BACKOFF_BASE_SECONDS * (2**attempt))
    raise RuntimeError(
        f"failed to download {series_id} after {config.MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc


def _fetch_and_store_series(
    conn, series_id: str, start: str | None, end: str | None, fetched_at: str
) -> None:
    if not config.FRED_API_KEY:
        repo.insert_data_gap(
            conn,
            ticker=series_id,
            expected_date=None,
            reason="FRED_API_KEY not set",
            detected_at=fetched_at,
        )
        return

    try:
        observations = _download_observations_with_retry(series_id, start, end)
    except Exception as exc:
        repo.insert_data_gap(
            conn,
            ticker=series_id,
            expected_date=None,
            reason=f"fetch failed: {exc}",
            detected_at=fetched_at,
        )
        return

    if not observations:
        repo.insert_data_gap(
            conn,
            ticker=series_id,
            expected_date=None,
            reason="no observations returned by FRED",
            detected_at=fetched_at,
        )
        return

    last_date = observations[-1]["date"]
    for obs in observations:
        value = _parse_value(obs.get("value"))
        repo.upsert_macro_observation(
            conn,
            series_id=series_id,
            obs_date=obs["date"],
            value=value,
            fetched_at=fetched_at,
        )
        if value is None:
            reason = (
                "latest observation not yet published (FRED publication lag)"
                if obs["date"] == last_date
                else "missing value returned by FRED"
            )
            repo.insert_data_gap(
                conn,
                ticker=series_id,
                expected_date=obs["date"],
                reason=reason,
                detected_at=fetched_at,
            )


def fetch_macro_series(
    conn,
    series_ids: Sequence[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
) -> None:
    """Fetch and store observations for each series in `series_ids`.

    Defaults to every series in config.FRED_SERIES_IDS. Series whose cache
    is still fresh (see ingest/cache.py) are skipped unless `force` is True.
    A failure on one series does not stop the batch.
    """
    if series_ids is None:
        series_ids = list(config.FRED_SERIES_IDS)

    for series_id in series_ids:
        if not force and not cache.should_refresh_macro(conn, series_id):
            continue
        fetched_at = cache.now_iso()
        _fetch_and_store_series(conn, series_id, start, end, fetched_at)
