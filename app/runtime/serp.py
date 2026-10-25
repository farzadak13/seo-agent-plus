"""SERP source selection.

An unconfigured SERP source raises at startup instead of silently disabling
the title path. ``static`` exists for controlled runs and acceptance tests,
where the snapshot must not change between runs; ``serper`` and
``dataforseo`` ask Google through a SERP API (app.serp.live), behind a
persistent cache and a daily budget (app.serp.cache).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.models.serp import SERPQuerySnapshot
from app.models.sites import SecretProvider, SecretRef
from app.serp.provider import StaticSERPProvider


class SERPNotConfiguredError(RuntimeError):
    """Raised when a SERP source is required but none is configured."""


def load_static_snapshots(path: str) -> dict[str, SERPQuerySnapshot]:
    source = Path(path)
    if not source.is_file():
        raise SERPNotConfiguredError(f"Static SERP snapshot file not found: {path}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SERPNotConfiguredError(f"Static SERP snapshot file is not valid JSON: {path}") from exc
    if not isinstance(raw, dict) or not raw:
        raise SERPNotConfiguredError(
            "Static SERP snapshot file must be a non-empty object mapping query -> snapshot."
        )
    snapshots: dict[str, SERPQuerySnapshot] = {}
    for query, payload in raw.items():
        snapshot = SERPQuerySnapshot.model_validate(payload)
        if snapshot.query != query:
            raise SERPNotConfiguredError(
                f"Static SERP snapshot key does not match its query field: {query}"
            )
        snapshots[query] = snapshot
    return snapshots


def build_serp_provider(config, *, repository=None, secret_resolver=None):
    """Build the configured SERP provider, or fail loudly when none is configured."""
    if config.serp_mode == "none":
        raise SERPNotConfiguredError(
            "SEO_AGENT_SERP_MODE is 'none'; the title path requires a SERP source."
        )
    if config.serp_mode == "static":
        if not config.serp_static_path:
            raise SERPNotConfiguredError(
                "SEO_AGENT_SERP_STATIC_PATH is required when SEO_AGENT_SERP_MODE is 'static'."
            )
        return StaticSERPProvider(
            load_static_snapshots(config.serp_static_path),
            provider_id="static",
        )
    if config.serp_mode in {"serper", "dataforseo"}:
        if repository is None or secret_resolver is None:
            raise SERPNotConfiguredError("A live SERP source needs the repository and secret resolver.")
        from app.keyword_intel.cache import RequestBudget
        from app.serp.cache import CachedSERPProvider
        from app.serp.live import DataForSEOSERPProvider, SerperSERPProvider

        secret = secret_resolver.resolve(
            SecretRef(provider=SecretProvider.ENVIRONMENT, key=config.serp_api_key_ref)
        )
        if config.serp_mode == "serper":
            live = SerperSERPProvider(
                api_key=secret, country=config.serp_country, language=config.serp_language
            )
        else:
            live = DataForSEOSERPProvider(
                credentials=secret, country=config.serp_country, language=config.serp_language
            )
        return CachedSERPProvider(
            live,
            repository,
            RequestBudget(repository, scope=f"serp:{live.provider_id}", daily_limit=config.serp_daily_budget),
            scope=f"{config.serp_country}-{config.serp_language}",
        )
    raise SERPNotConfiguredError(f"Unsupported SERP mode: {config.serp_mode}")
