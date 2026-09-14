from app.models.outcomes import OutcomeMetric


def benefit_change(metric, relative_change):
    """Keep stored raw deltas unchanged; orient only scoring toward improvement."""
    if relative_change is None:
        return None
    return -relative_change if metric == OutcomeMetric.POSITION else relative_change
