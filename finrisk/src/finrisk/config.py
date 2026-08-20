"""Central configuration: paths, cache TTLs, FRED series ids, and env var access.

All secrets (API keys) are read from environment variables only. Nothing in
this module hardcodes a key, and no default value here is a valid key.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths ---------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DB_PATH = Path(os.environ.get("FINRISK_DB_PATH") or (PROJECT_ROOT / "data" / "finrisk.db"))

SCHEMA_PATH = Path(__file__).resolve().parent / "db" / "schema.sql"

# --- Cache freshness -------------------------------------------------------
# Applies to both prices_weekly and macro_series. A single number here
# because ingest/cache.py is the sole place that reads it and decides
# whether a refresh is needed.

CACHE_TTL_DAYS = int(os.environ.get("FINRISK_CACHE_TTL_DAYS") or 7)

# --- Retry / backoff for network calls ------------------------------------

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0

# --- FRED -------------------------------------------------------------

FRED_API_KEY = os.environ.get("FRED_API_KEY")

FRED_API_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# Series tracked in this phase: short/long rates, inflation, unemployment,
# and the policy rate. Keys are FRED series ids, values are human labels.
FRED_SERIES_IDS: dict[str, str] = {
    "DGS3MO": "3-Month Treasury Constant Maturity Rate",
    "DGS10": "10-Year Treasury Constant Maturity Rate",
    "CPIAUCSL": "Consumer Price Index for All Urban Consumers",
    "UNRATE": "Unemployment Rate",
    "FEDFUNDS": "Effective Federal Funds Rate",
}
