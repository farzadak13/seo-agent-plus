from collections.abc import Sequence

from app.models.gsc import NormalizedGSCRow
from app.models.observations import DataStatus, DailyObservation


def rows_to_observations(
    rows: Sequence[NormalizedGSCRow],
) -> list[DailyObservation]:
    """
    Convert normalized GSC rows into domain observations.

    The source rows are already normalized. This step applies
    domain-level validation and assigns the observed data status.
    """

    observations: list[DailyObservation] = []

    for row in rows:
        observations.append(
            DailyObservation(
                site_id=row.site_id,
                normalized_url=row.normalized_url,
                normalized_query=row.normalized_query,
                date=row.date,
                impressions=row.impressions,
                clicks=row.clicks,
                avg_position=row.avg_position,
                data_status=DataStatus.OBSERVED,
            )
        )

    return observations