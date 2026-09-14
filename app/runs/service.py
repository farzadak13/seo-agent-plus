from __future__ import annotations

from datetime import date

from app.models.pipeline import DecisionPipelineResult
from app.models.sites import Site
from app.onboarding.secrets import SecretResolver
from app.runs.contracts import BaselineProvider, GSCDataGateway, PipelineRunner


class RunServiceError(RuntimeError):
    pass


class SEORunService:
    """
    Application service for an SEO decision run.

    Infrastructure dependencies are injected. This layer coordinates the
    connectors and the deterministic decision pipeline but does not contain
    SEO scoring or transport-specific code.
    """

    def __init__(
        self,
        *,
        secret_resolver: SecretResolver,
        gsc_gateway: GSCDataGateway,
        baseline_provider: BaselineProvider,
        pipeline_runner: PipelineRunner,
    ) -> None:
        self._secret_resolver = secret_resolver
        self._gsc_gateway = gsc_gateway
        self._baseline_provider = baseline_provider
        self._pipeline_runner = pipeline_runner

    def run(
        self,
        *,
        site: Site,
        start_date: date,
        end_date: date,
        normalized_url: str,
        normalized_query: str,
        candidate_id: str,
    ) -> DecisionPipelineResult:
        if site.gsc is None:
            raise RunServiceError("GSC is not configured for this site.")

        credential = self._secret_resolver.resolve(site.gsc.credential_ref)

        response = self._gsc_gateway.fetch(
            site_id=site.site_id,
            property_url=str(site.gsc.property_url),
            credential=credential,
            start_date=start_date,
            end_date=end_date,
        )

        baseline_observations, baseline_url_metrics = (
            self._baseline_provider.load(
                site_id=site.site_id,
                normalized_url=normalized_url,
                normalized_query=normalized_query,
                end_date=end_date,
            )
        )

        return self._pipeline_runner.run(
            response=response,
            start_date=start_date,
            end_date=end_date,
            baseline_observations=baseline_observations,
            baseline_url_metrics=baseline_url_metrics,
            candidate_id=candidate_id,
            normalized_url=normalized_url,
            normalized_query=normalized_query,
        )
