-- Search Console time series, one row per day and device, for the dashboard.
--
-- Not in persistence_records: a dashboard aggregates months of rows by page
-- and query, which a JSON record store cannot index. These are plain tables
-- with keys that say what a row is.
--
-- Position is stored as position_sum = position x impressions, never as an
-- average: averaging daily averages over a week gives the wrong answer, and
-- the right one, sum(position_sum) / sum(impressions), needs the sum. CTR is
-- not stored at all; it is clicks / impressions, computed when read.
--
-- Days are Search Console's days (Pacific time), stored as Google reports
-- them. Device is Search Console's: DESKTOP, MOBILE, TABLET.

-- Each URL and each query once per site, normalised. Rows refer to them by id,
-- so a long URL is not repeated on every row of every day.
CREATE TABLE IF NOT EXISTS gsc_pages (
    page_id BIGSERIAL PRIMARY KEY,
    site_id TEXT NOT NULL,
    url TEXT NOT NULL,
    CONSTRAINT gsc_pages_site_url_unique UNIQUE (site_id, url)
);

CREATE TABLE IF NOT EXISTS gsc_queries (
    query_id BIGSERIAL PRIMARY KEY,
    site_id TEXT NOT NULL,
    query TEXT NOT NULL,
    CONSTRAINT gsc_queries_site_query_unique UNIQUE (site_id, query)
);

-- Site totals, from a request without the query dimension. Not the sum of the
-- query rows: Search Console leaves anonymised queries out of those (14% of
-- impressions on the first site measured), and a chart built from them would
-- be low by that much with nothing to show it.
CREATE TABLE IF NOT EXISTS gsc_daily_totals (
    site_id TEXT NOT NULL,
    day DATE NOT NULL,
    device TEXT NOT NULL,
    clicks BIGINT NOT NULL CHECK (clicks >= 0),
    impressions BIGINT NOT NULL CHECK (impressions >= 0),
    position_sum DOUBLE PRECISION NOT NULL CHECK (position_sum >= 0),
    PRIMARY KEY (site_id, day, device)
);

CREATE TABLE IF NOT EXISTS gsc_daily_pages (
    site_id TEXT NOT NULL,
    day DATE NOT NULL,
    page_id BIGINT NOT NULL REFERENCES gsc_pages (page_id),
    device TEXT NOT NULL,
    clicks BIGINT NOT NULL CHECK (clicks >= 0),
    impressions BIGINT NOT NULL CHECK (impressions >= 0),
    position_sum DOUBLE PRECISION NOT NULL CHECK (position_sum >= 0),
    PRIMARY KEY (site_id, day, page_id, device)
);

CREATE TABLE IF NOT EXISTS gsc_daily_queries (
    site_id TEXT NOT NULL,
    day DATE NOT NULL,
    page_id BIGINT NOT NULL REFERENCES gsc_pages (page_id),
    query_id BIGINT NOT NULL REFERENCES gsc_queries (query_id),
    device TEXT NOT NULL,
    clicks BIGINT NOT NULL CHECK (clicks >= 0),
    impressions BIGINT NOT NULL CHECK (impressions >= 0),
    position_sum DOUBLE PRECISION NOT NULL CHECK (position_sum >= 0),
    PRIMARY KEY (site_id, day, page_id, query_id, device)
);

-- "Top pages over a range" and "this page's queries over a range" read by
-- site and day first; "this query's history" reads by query.
CREATE INDEX IF NOT EXISTS idx_gsc_daily_queries_query
    ON gsc_daily_queries (site_id, query_id, day);

-- One row per site and day that has been fetched: what the sync knows. A day
-- is synced when its final data is stored; the property it was fetched from
-- is recorded so that changing a site's property re-fetches its history.
CREATE TABLE IF NOT EXISTS gsc_sync_days (
    site_id TEXT NOT NULL,
    day DATE NOT NULL,
    property_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('synced', 'failed')),
    totals_rows INTEGER NOT NULL DEFAULT 0,
    page_rows INTEGER NOT NULL DEFAULT 0,
    query_rows INTEGER NOT NULL DEFAULT 0,
    -- Search Console returns at most 50,000 rows a day per request; a day
    -- that reached it is complete only up to that cap, and says so.
    page_rows_capped BOOLEAN NOT NULL DEFAULT FALSE,
    query_rows_capped BOOLEAN NOT NULL DEFAULT FALSE,
    fetched_at TIMESTAMPTZ NOT NULL,
    error TEXT NULL,
    PRIMARY KEY (site_id, day)
);
