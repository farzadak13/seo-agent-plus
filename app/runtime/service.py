"""Bound per-run gateways prevent credentials and baselines leaking between sites."""
from datetime import date as Date, timedelta
from hashlib import sha256
import json

from app.models.persistence import PersistenceRecord
from app.models.observations import DailyObservation, DataStatus
from app.models.snapshots import SnapshotMetadata
from app.models.sites import SiteStatus
from app.normalization.url import canonicalize_url
from app.normalization.query import normalize_query
from app.normalization.gsc import normalize_gsc_response, normalize_url_gsc_response
from app.runtime.data_quality import validate_final_response, validate_daily_totals
from app.normalization.observation import rows_to_observations
from app.pipeline.decision import run_decision_pipeline
from app.persistence.contracts import PersistenceConflictError
from app.ingestion.calendar import latest_final_date
from app.runtime.gsc import LiveGSCGateway, build_google_transport


class PersistentSEORunService:
    def __init__(
        self,
        *,
        repository,
        secret_resolver,
        settings,
        gateway_factory=None,
        transport=None,
        today_fn=Date.today,
    ):
        self.repository = repository
        self.secret_resolver = secret_resolver
        self.settings = settings
        self.today_fn = today_fn
        self._transport = transport
        self.gateway_factory = gateway_factory or self._default_gateway

    def _default_gateway(self, site):
        # One transport for the whole runtime: one connection pool, and the
        # proxy configured in exactly one place.
        if self._transport is None:
            self._transport = build_google_transport(self.settings)
        return LiveGSCGateway(
            site,
            timeout=self.settings.gsc_timeout_seconds,
            request_fn=self._transport,
        )

    def _save_response(self, response):
        try:
            self.repository.create(PersistenceRecord(record_id=response.response_id,
                aggregate_type="runtime_gsc_response", aggregate_id=response.response_id,
                payload=response.model_dump(mode="json")))
        except PersistenceConflictError:
            # Content-addressed responses can be fetched again on retry.
            existing = self.repository.get(aggregate_type="runtime_gsc_response", aggregate_id=response.response_id)
            if (existing is None or existing.payload.get("site_id") != response.site_id
                    or existing.payload.get("raw_payload") != response.raw_payload):
                raise

    def run(self, *, site, start_date, end_date, normalized_url, normalized_query, candidate_id):
        if site.status != SiteStatus.ACTIVE:
            raise ValueError("Site must be active to run analysis.")
        if site.gsc is None:
            raise ValueError("GSC is not configured.")
        days = (end_date - start_date).days + 1
        if not 1 <= days <= self.settings.max_window_days:
            raise ValueError("Run date window is invalid or exceeds configured limit.")
        # Search Console keeps revising recent days. A window that reaches into
        # them reads as a traffic drop that never happened, and the engine
        # recommends a fix for it. Refuse rather than decide on moving numbers.
        newest_settled = latest_final_date(
            self.today_fn(), lag_days=self.settings.gsc_data_lag_days
        )
        if end_date > newest_settled:
            raise ValueError(
                f"Search Console data through {end_date.isoformat()} has not settled; "
                f"the newest usable day is {newest_settled.isoformat()}."
            )
        url = canonicalize_url(normalized_url)
        query = normalize_query(normalized_query)
        if not query:
            raise ValueError("Target query cannot be empty.")
        gateway = self.gateway_factory(site)
        credential = self.secret_resolver.resolve(site.gsc.credential_ref)
        baseline_end = start_date - timedelta(days=1)
        baseline_start = start_date - timedelta(days=days)

        def fetch(begin, end, *, url_level=False):
            method = gateway.fetch_url_metrics if url_level else gateway.fetch
            response = method(site_id=site.site_id, property_url=str(site.gsc.property_url),
                              credential=credential, start_date=begin, end_date=end)
            validate_final_response(response, site_id=site.site_id, begin=begin, end=end)
            normalizer = normalize_url_gsc_response if url_level else normalize_gsc_response
            normalized = normalizer(response)
            if any(not begin <= row.date <= end for row in normalized):
                raise ValueError("GSC row date is outside requested window.")
            self._save_response(response)
            return response, normalized

        baseline, baseline_rows = fetch(baseline_start, baseline_end)
        baseline_urls, baseline_metrics = fetch(baseline_start, baseline_end, url_level=True)
        current, current_rows = fetch(start_date, end_date)
        current_urls, current_metrics = fetch(start_date, end_date, url_level=True)
        baseline_metrics = [r for r in baseline_metrics if r.normalized_url == url]
        current_metrics = [r for r in current_metrics if r.normalized_url == url]
        validate_daily_totals([r for r in baseline_rows if r.normalized_url == url], baseline_metrics)
        validate_daily_totals([r for r in current_rows if r.normalized_url == url], current_metrics)
        baseline_target = [r for r in baseline_rows if r.normalized_url == url and r.normalized_query == query]
        observations = rows_to_observations(baseline_target)
        observed_days = {r.date for r in observations}
        for offset in range(days):
            day = baseline_start + timedelta(days=offset)
            if day not in observed_days:
                observations.append(DailyObservation(site_id=site.site_id, normalized_url=url,
                    normalized_query=query, date=day, data_status=DataStatus.MISSING_UNKNOWN))
        observations.sort(key=lambda row: row.date)
        selected = [{"keys": [row.normalized_url, row.normalized_query, row.date.isoformat()],
                     "impressions": row.impressions, "clicks": row.clicks, "position": row.avg_position}
                    for row in current_rows if row.normalized_url == url and row.normalized_query == query]
        source_ids = [r.response_id for r in (baseline, baseline_urls, current, current_urls)]
        scoped = current.model_copy(update={"raw_payload": {
            **current.raw_payload, "rows": selected,
            "source_response_ids": source_ids,
        }})
        identity = sha256(json.dumps([source_ids, url, query, "stage35-v1"], ensure_ascii=False).encode()).hexdigest()
        bundle_id = "bundle:" + identity
        bundle = PersistenceRecord(record_id=bundle_id, aggregate_type="runtime_data_bundle",
            aggregate_id=bundle_id, payload={"site_id": site.site_id, "normalized_url": url,
                "normalized_query": query, "source_response_ids": source_ids,
                "baseline_start": baseline_start.isoformat(), "baseline_end": baseline_end.isoformat(),
                "current_start": start_date.isoformat(), "current_end": end_date.isoformat(),
                "data_state": "final", "quality_policy": "stage35-v1"})
        try:
            self.repository.create(bundle)
        except PersistenceConflictError:
            existing = self.repository.get(aggregate_type="runtime_data_bundle", aggregate_id=bundle_id)
            if existing is None or existing.payload != bundle.payload:
                raise
        return run_decision_pipeline(response=scoped, start_date=start_date, end_date=end_date,
            baseline_observations=observations, baseline_url_metrics=baseline_metrics,
            current_url_metrics=current_metrics,
            snapshot=SnapshotMetadata(snapshot_id="runtime:"+identity, data_snapshot_id=bundle_id,
                rule_version="rules-v1", config_version="runtime-stage35-v1", generated_at=current.created_at),
            candidate_id=candidate_id, normalized_url=url, normalized_query=query)
