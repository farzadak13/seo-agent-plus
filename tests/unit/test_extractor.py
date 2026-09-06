import pytest
from datetime import date

from app.features.extractor import extract_feature_set
from app.models.observations import DailyObservation, DataStatus
from app.models.url_metrics import URLDailyMetric


def observation(
    day: int,
    impressions: int,
    clicks: int,
    position: float,
) -> DailyObservation:
    return DailyObservation(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        date=date(2026, 8, day),
        impressions=impressions,
        clicks=clicks,
        avg_position=position,
        data_status=DataStatus.OBSERVED,
    )

def url_metric(
    day: int,
    impressions: int,
    clicks: int,
) -> URLDailyMetric:
    return URLDailyMetric(
        site_id="site-1",
        normalized_url="https://example.com/page",
        date=date(2026, 8, day),
        total_impressions=impressions,
        total_clicks=clicks,
        data_status=DataStatus.OBSERVED,
    )


def test_extract_feature_set():
    baseline = [
        observation(1, 100, 10, 3.0),
        observation(2, 100, 10, 4.0),
    ]

    current = [
        observation(8, 200, 12, 6.0),
        observation(9, 200, 12, 7.0),
    ]

    baseline_urls = [
        url_metric(1, 500, 50),
        url_metric(2, 500, 50),
    ]

    current_urls = [
        url_metric(8, 800, 80),
        url_metric(9, 800, 80),
    ]

    features = extract_feature_set(
        baseline_observations=baseline,
        current_observations=current,
        baseline_url_metrics=baseline_urls,
        current_url_metrics=current_urls,
    )


def test_extractor_marks_zero_baseline_ctr():
    baseline = [
        observation(1, 0, 0, 3.0),
        observation(2, 0, 0, 4.0),
    ]

    current = [
        observation(8, 100, 10, 3.0),
        observation(9, 100, 10, 3.0),
    ]

    baseline_urls = [
        url_metric(1, 0, 0),
        url_metric(2, 0, 0),
    ]

    current_urls = [
        url_metric(8, 100, 10),
        url_metric(9, 100, 10),
    ]

    features = extract_feature_set(
        baseline_observations=baseline,
        current_observations=current,
        baseline_url_metrics=baseline_urls,
        current_url_metrics=current_urls,
    )

    assert features.ctr.baseline_ctr_zero is True
    assert features.ctr.baseline_ctr == 0.0
    assert features.ctr.current_ctr == 0.1


def test_extractor_is_order_independent_for_position_trend():
    observations = [
        observation(3, 100, 10, 6.0),
        observation(1, 100, 10, 2.0),
        observation(2, 100, 10, 4.0),
    ]

    features = extract_feature_set(
        baseline_observations=[
            observation(1, 100, 10, 2.0),
        ],
        current_observations=observations,
        baseline_url_metrics=[
            url_metric(1, 100, 10),
        ],
        current_url_metrics=[
            url_metric(1, 300, 30),
            url_metric(2, 300, 30),
            url_metric(3, 300, 30),
        ],
    )

    assert features.position.trend_direction == "declining"