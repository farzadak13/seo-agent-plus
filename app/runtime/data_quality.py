from collections import defaultdict
from datetime import date


def validate_final_response(response, *, site_id, begin, end):
    if response.site_id != site_id:
        raise ValueError("GSC response belongs to another site.")
    payload = response.raw_payload
    if payload.get("dataState") != "final":
        raise ValueError("Live decisions require final GSC data.")
    if payload.get("startDate") != begin.isoformat() or payload.get("endDate") != end.isoformat():
        raise ValueError("GSC response window does not match request.")
    if payload.get("responseAggregationType") not in {None, "auto", "byPage"}:
        raise ValueError("Page-level GSC aggregation is required.")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Invalid GSC metadata.")
    first = metadata.get("first_incomplete_date")
    if first is not None and date.fromisoformat(first) <= end:
        raise ValueError("Requested window includes incomplete GSC data.")


def validate_daily_totals(query_rows, url_metrics):
    """Compare every date independently; differences cannot cancel across dates."""
    query_totals = defaultdict(lambda: [0, 0])
    for row in query_rows:
        key = (row.site_id, row.normalized_url, row.date)
        query_totals[key][0] += row.impressions
        query_totals[key][1] += row.clicks
    pages = {(row.site_id, row.normalized_url, row.date): row for row in url_metrics}
    for key, (impressions, clicks) in query_totals.items():
        page = pages.get(key)
        if page is None:
            raise ValueError("Independent URL metrics are missing for an observed query date.")
        if impressions > page.total_impressions or clicks > page.total_clicks:
            raise ValueError("Query totals exceed independent URL totals on a date.")
