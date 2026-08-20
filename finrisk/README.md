# finrisk — data layer

Data layer for a portfolio risk analytics tool: fetching, storing, and
caching weekly ETF prices and FRED macro series in SQLite.

**Out of scope for this phase:** risk metric calculations (Sharpe,
drawdown, etc.), LLM integration, any UI, backtesting, and anything that
produces a buy/sell signal. This tool computes risk metrics for a
portfolio the user already holds — it does not recommend trades or pick
assets.

## Requirements

- Python 3.11+
- A [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html) (free) for macro data

## Setup

```bash
cd finrisk
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env` and set `FRED_API_KEY`. Never commit `.env`.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `FRED_API_KEY` | yes (for macro ingest) | — | FRED API authentication |
| `FINRISK_DB_PATH` | no | `<repo>/data/finrisk.db` | SQLite database file path |
| `FINRISK_CACHE_TTL_DAYS` | no | `7` | Days before cached prices/macro data are refetched |

## Running

The database and schema are created automatically on first connection —
no separate migration step.

```python
from finrisk.db.connection import get_connection
from finrisk.ingest.prices import fetch_weekly_prices
from finrisk.ingest.macro import fetch_macro_series

with get_connection() as conn:
    fetch_weekly_prices(conn, ["SPY", "AGG", "GLD"], start="2023-01-01", end="2024-01-01")
    fetch_macro_series(conn)  # defaults to all series in config.FRED_SERIES_IDS
```

Each call skips tickers/series whose cached data is still within the
freshness window (`FINRISK_CACHE_TTL_DAYS`); pass `force=True` to bypass
the cache and refetch regardless of age.

## Data rules

- **Missing data is never filled in.** No zero-fill, no forward-fill, no
  interpolation. A missing value stays `NULL`, and the reason is recorded
  in `data_gaps`.
- **`adj_close` is the field downstream code should use.** It is adjusted
  for dividends/splits; `close` is stored for reference only and will
  understate returns if used directly.
- **All dates are ISO 8601 text** (`YYYY-MM-DD`). `prices_weekly.week_end`
  is always a Friday date — if Friday wasn't a trading day, that week's
  last trading day's data is stored under the Friday date.
- **`data_gaps.ticker`** holds either an ETF ticker or, for macro rows, a
  FRED series id (the table has no separate `series_id` column).

## Tests

```bash
pytest
```

All tests mock external calls (`yfinance`, FRED HTTP requests) and use a
temporary SQLite file — no network access or API key is needed to run the
suite.
