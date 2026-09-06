from datetime import date as Date, timedelta


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