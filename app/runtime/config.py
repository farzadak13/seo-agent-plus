from __future__ import annotations

import os
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.gsc.transport import GOOGLE_API_BASE
from app.ingestion.calendar import GSC_DATA_LAG_DAYS


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: str = Field(default="development", min_length=1, max_length=50)
    api_key: str = Field(min_length=1, repr=False)
    database_dsn: str = Field(min_length=1, repr=False)
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = Field(default=1.0, ge=0.0, le=3600.0)
    gsc_mode: str = Field(default="stub", pattern=r"^(stub|live)$")
    observability_enabled: bool = True
    gsc_timeout_seconds: float = Field(default=30.0, gt=0)
    max_window_days: int = Field(default=90, ge=1, le=365)

    # Egress — where Google calls go and through what. A proxy is a setting on
    # one session rather than a second code path, so development from a place
    # that cannot reach googleapis.com runs the same code as production.
    google_api_base: str = Field(default=GOOGLE_API_BASE, min_length=1)
    google_proxy_url: str | None = Field(default=None, repr=False)
    google_verify_tls: bool = True

    # How far back the newest usable day is. See app.ingestion.calendar.
    gsc_data_lag_days: int = Field(default=GSC_DATA_LAG_DAYS, ge=0, le=10)

    # Stage 36 — title recommendation path.
    title_workflow_enabled: bool = False
    serp_mode: str = Field(default="none", pattern=r"^(none|static)$")
    serp_static_path: str | None = None
    llm_mode: str = Field(default="none", pattern=r"^(none|arvan)$")
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    reasoning_config_version: str = Field(default="runtime-stage36-v1", min_length=1)
    arvan_endpoint: str | None = None
    arvan_model: str | None = None
    arvan_api_key_ref: str | None = Field(default=None, repr=False)

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if not value.strip() or value.strip() == "change-me":
            raise ValueError("A non-default API key is required.")
        return value

    @model_validator(mode="after")
    def egress_is_safe_for_the_environment(self):
        """TLS verification is turned off to get through a local proxy, and
        then nobody remembers to turn it back on. Production refuses."""
        if self.environment.strip().lower() == "production" and not self.google_verify_tls:
            raise ValueError(
                "SEO_AGENT_GOOGLE_VERIFY_TLS must not be false in production: "
                "an unverified proxy can read and rewrite the token traffic."
            )
        return self

    @model_validator(mode="after")
    def title_path_is_fully_configured(self):
        """The title path must be wholly on or wholly off — never half wired."""
        if self.title_workflow_enabled:
            if self.serp_mode == "none":
                raise ValueError(
                    "SEO_AGENT_SERP_MODE must not be 'none' when the title workflow is enabled."
                )
            if self.llm_mode == "none":
                raise ValueError(
                    "SEO_AGENT_LLM_MODE must not be 'none' when the title workflow is enabled."
                )
        if self.serp_mode == "static" and not self.serp_static_path:
            raise ValueError(
                "SEO_AGENT_SERP_STATIC_PATH is required when SEO_AGENT_SERP_MODE is 'static'."
            )
        if self.llm_mode == "arvan":
            missing = [
                name
                for name, value in (
                    ("SEO_AGENT_ARVAN_ENDPOINT", self.arvan_endpoint),
                    ("SEO_AGENT_ARVAN_MODEL", self.arvan_model),
                    ("SEO_AGENT_ARVAN_API_KEY_REF", self.arvan_api_key_ref),
                )
                if value is None or not str(value).strip()
            ]
            if missing:
                raise ValueError(
                    "Missing Arvan reasoning settings: " + ", ".join(missing)
                )
        return self

    @classmethod
    def from_environment(cls) -> "RuntimeConfig":
        api_key = os.getenv("SEO_AGENT_API_KEY")
        database_dsn = os.getenv("SEO_AGENT_DATABASE_DSN")
        environment = os.getenv("SEO_AGENT_ENV", "development").strip() or "development"

        if api_key is None or not api_key.strip():
            raise RuntimeError("SEO_AGENT_API_KEY is required; no default API key is allowed.")
        if database_dsn is None or not database_dsn.strip():
            raise RuntimeError("SEO_AGENT_DATABASE_DSN is required for the application runtime.")
        if api_key.strip() == "change-me":
            raise RuntimeError("SEO_AGENT_API_KEY must not use the insecure default 'change-me'.")

        return cls(
            environment=environment,
            api_key=api_key,
            database_dsn=database_dsn,
            worker_enabled=_env_bool("SEO_AGENT_WORKER_ENABLED", True),
            worker_poll_interval_seconds=float(os.getenv("SEO_AGENT_WORKER_POLL_INTERVAL_SECONDS", "1.0")),
            gsc_mode=os.getenv("SEO_AGENT_GSC_MODE", "stub").strip().lower() or "stub",
            observability_enabled=_env_bool("SEO_AGENT_OBSERVABILITY_ENABLED", True),
            gsc_timeout_seconds=float(os.getenv("SEO_AGENT_GSC_TIMEOUT_SECONDS", "30")),
            max_window_days=int(os.getenv("SEO_AGENT_MAX_WINDOW_DAYS", "90")),
            google_api_base=(
                os.getenv("SEO_AGENT_GOOGLE_API_BASE", GOOGLE_API_BASE).strip() or GOOGLE_API_BASE
            ),
            google_proxy_url=_env_optional("SEO_AGENT_GOOGLE_PROXY_URL"),
            google_verify_tls=_env_bool("SEO_AGENT_GOOGLE_VERIFY_TLS", True),
            gsc_data_lag_days=int(
                os.getenv("SEO_AGENT_GSC_DATA_LAG_DAYS", str(GSC_DATA_LAG_DAYS))
            ),
            title_workflow_enabled=_env_bool("SEO_AGENT_TITLE_WORKFLOW_ENABLED", False),
            serp_mode=os.getenv("SEO_AGENT_SERP_MODE", "none").strip().lower() or "none",
            serp_static_path=_env_optional("SEO_AGENT_SERP_STATIC_PATH"),
            llm_mode=os.getenv("SEO_AGENT_LLM_MODE", "none").strip().lower() or "none",
            llm_timeout_seconds=float(os.getenv("SEO_AGENT_LLM_TIMEOUT_SECONDS", "60")),
            reasoning_config_version=(
                os.getenv("SEO_AGENT_REASONING_CONFIG_VERSION", "runtime-stage36-v1").strip()
                or "runtime-stage36-v1"
            ),
            arvan_endpoint=_env_optional("SEO_AGENT_ARVAN_ENDPOINT"),
            arvan_model=_env_optional("SEO_AGENT_ARVAN_MODEL"),
            arvan_api_key_ref=_env_optional("SEO_AGENT_ARVAN_API_KEY_REF"),
        )


def _env_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean value.")


def load_runtime_config() -> RuntimeConfig:
    return RuntimeConfig.from_environment()
