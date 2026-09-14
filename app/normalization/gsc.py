"""Validate source keys, deduplicate retransmissions, then merge canonical identities."""
from collections.abc import Sequence
from datetime import date
from math import isfinite

from app.models.gsc import NormalizedGSCRow, RawGSCResponse
from app.models.url_metrics import URLDailyMetric
from app.normalization.query import normalize_query
from app.normalization.url import canonicalize_url


def _groups(response, *, query_level):
    rows = response.raw_payload.get("rows", [])
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("GSC payload 'rows' must be a sequence")
    expected = ["page", "query", "date"] if query_level else ["page", "date"]
    dimensions = response.raw_payload.get("dimensions")
    if dimensions is not None and dimensions != expected:
        # Legacy query payloads without date remain supported explicitly.
        if not (query_level and dimensions == ["page", "query"]):
            raise ValueError("Unexpected GSC dimensions")
    seen = {}
    groups = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each GSC row must be an object")
        keys = row.get("keys")
        lengths = {2, 3} if query_level and dimensions != expected else {len(expected)}
        if not isinstance(keys, list) or len(keys) not in lengths or not all(isinstance(k, str) for k in keys):
            raise ValueError("Invalid GSC row keys")
        impressions, clicks = row.get("impressions"), row.get("clicks")
        if any(type(value) is not int or value < 0 for value in (impressions, clicks)):
            raise ValueError("GSC counts must be non-negative integers")
        if clicks > impressions:
            raise ValueError("GSC clicks exceed impressions")
        position = row.get("position")
        if position is not None and (isinstance(position, bool) or not isinstance(position, (int, float))
                                     or not isfinite(position) or position < 0):
            raise ValueError("GSC position must be finite, non-negative or null")
        raw_key = tuple(keys)
        values = (impressions, clicks, position)
        if raw_key in seen:
            if seen[raw_key] != values:
                raise ValueError("Conflicting duplicate GSC row")
            continue
        seen[raw_key] = values
        day = date.fromisoformat(keys[-1]) if not query_level or len(keys) == 3 else response.fetch_date
        query = normalize_query(keys[1]) if query_level else ""
        key = (canonicalize_url(keys[0]), query, day)
        groups.setdefault(key, []).append(values)
    return groups


def normalize_gsc_response(response: RawGSCResponse) -> list[NormalizedGSCRow]:
    result = []
    for (url, query, day), values in sorted(_groups(response, query_level=True).items()):
        impressions = sum(v[0] for v in values)
        if len(values) == 1:
            position = values[0][2]
        elif impressions and all(v[2] is not None for v in values if v[0]):
            position = sum(v[0] * v[2] for v in values if v[0]) / impressions
        else:
            position = None
        result.append(NormalizedGSCRow(site_id=response.site_id, date=day, normalized_url=url,
            normalized_query=query, impressions=impressions, clicks=sum(v[1] for v in values), avg_position=position))
    return result


def normalize_url_gsc_response(response: RawGSCResponse) -> list[URLDailyMetric]:
    return [URLDailyMetric(site_id=response.site_id, normalized_url=url, date=day,
                          total_impressions=sum(v[0] for v in values), total_clicks=sum(v[1] for v in values))
            for (url, _, day), values in sorted(_groups(response, query_level=False).items())]
