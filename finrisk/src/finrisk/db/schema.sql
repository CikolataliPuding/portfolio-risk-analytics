-- finrisk database schema.
--
-- All dates are stored as ISO 8601 text (YYYY-MM-DD).
--
-- prices_weekly.week_end is always a Friday date. If Friday was not a
-- trading day (holiday), the last trading day's data for that week is
-- written under the Friday date -- the row key is always the calendar
-- Friday, not the actual trading date.
--
-- prices_weekly.adj_close is the primary field for all downstream
-- calculations (returns, risk metrics, etc.) because it is adjusted for
-- dividends and splits; `close` is stored for reference only and will
-- systematically understate returns if used for performance calculations.
--
-- Missing data is never filled (no zero-fill, no forward-fill, no
-- interpolation). A missing value stays NULL and the gap is recorded in
-- data_gaps with a reason.

CREATE TABLE IF NOT EXISTS instruments (
    ticker          TEXT PRIMARY KEY,
    name            TEXT,
    asset_class     TEXT,
    region          TEXT,
    currency        TEXT DEFAULT 'USD',
    is_active       INTEGER DEFAULT 1,
    last_updated    TEXT
);

CREATE TABLE IF NOT EXISTS prices_weekly (
    ticker          TEXT NOT NULL,
    week_end        TEXT NOT NULL,
    adj_close       REAL,
    close           REAL,
    volume          REAL,
    source          TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (ticker, week_end),
    FOREIGN KEY (ticker) REFERENCES instruments(ticker)
);

CREATE TABLE IF NOT EXISTS macro_series (
    series_id       TEXT NOT NULL,
    obs_date        TEXT NOT NULL,
    value           REAL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (series_id, obs_date)
);

CREATE TABLE IF NOT EXISTS data_gaps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT,
    expected_date   TEXT,
    reason          TEXT,
    detected_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prices_week ON prices_weekly(week_end);
CREATE INDEX IF NOT EXISTS idx_macro_date  ON macro_series(obs_date);
