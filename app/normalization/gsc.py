from collections.abc import Sequence

from app.models.gsc import NormalizedGSCRow, RawGSCResponse
from app.normalization.query import normalize_query
from app.normalization.url import canonicalize_url


def normalize_gsc_response(
    response: RawGSCResponse,
) -> list[NormalizedGSCRow]:
    """
    Convert a raw GSC response into normalized query-level rows.

    Only deterministic normalization is performed.
    No semantic rewriting or SEO inference is performed.
    """

    rows = response.raw_payload.get("rows", [])

    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("GSC payload 'rows' must be a sequence")

    normalized_rows: list[NormalizedGSCRow] = []

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each GSC row must be an object")

        keys = row.get("keys")

        if not isinstance(keys, list) or len(keys) < 2:
            raise ValueError(
                "Each GSC row must contain URL and query keys"
            )

        raw_url = keys[0]
        raw_query = keys[1]

        normalized_url = canonicalize_url(raw_url)
        normalized_query = normalize_query(raw_query)

        impressions = row.get("impressions")
        clicks = row.get("clicks")
        avg_position = row.get("position")

        if not isinstance(impressions, int):
            raise ValueError("GSC impressions must be an integer")

        if not isinstance(clicks, int):
            raise ValueError("GSC clicks must be an integer")

        if avg_position is not None and not isinstance(
            avg_position,
            (int, float),
        ):
            raise ValueError("GSC position must be numeric or null")

        normalized_rows.append(
            NormalizedGSCRow(
                site_id=response.site_id,
                date=response.fetch_date,
                normalized_url=normalized_url,
                normalized_query=normalized_query,
                impressions=impressions,
                clicks=clicks,
                avg_position=(
                    float(avg_position)
                    if avg_position is not None
                    else None
                ),
            )
        )

    return normalized_rows