from datetime import date as Date, timedelta

# Search Console keeps revising a day's numbers after the day ends. Google does
# not publish a guaranteed settling time, but three days is what `dataState`
# behaviour shows in practice: ask for yesterday and `final` still moves.
#
# This matters more here than it looks. The whole system compares a window
# against a baseline window and decides whether something changed. If the
# recent window is still filling in, impressions are low for a reason that has
# nothing to do with the site, and the engine reports a drop that does not
# exist. A wrong recommendation is worse than a late one.
GSC_DATA_LAG_DAYS = 3


def latest_final_date(today: Date, *, lag_days: int = GSC_DATA_LAG_DAYS) -> Date:
    """The newest date whose Search Console data can be treated as settled."""
    if lag_days < 0:
        raise ValueError("lag_days must not be negative")
    return today - timedelta(days=lag_days)


def expand_date_range(
    start_date: Date,
    end_date: Date,
) -> list[Date]:
    """
    Expand an inclusive date range into a sorted list of daily dates.

    The function is deterministic and does not infer anything
    about data availability.
    """

    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    dates: list[Date] = []
    current = start_date

    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)

    return dates
