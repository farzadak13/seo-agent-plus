"""Search Console time series for the dashboard (migration 004).

    models.py  one day of data, in additive metrics
    fetch.py   three Search Console requests for one day, normalised
    store.py   PostgreSQL: a day replaced whole, and the sync ledger
    sync.py    fetch then store, or record the failure
"""
