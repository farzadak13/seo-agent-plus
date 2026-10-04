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

    # Sign in with Google. All three, or none: a half-configured client sends
    # customers to Google and fails on the way back.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = Field(default=None, repr=False)
    public_base_url: str | None = None

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

    # Keyword demand and rank tracking. The provider is a setting, not code:
    # see app.keyword_intel.
    keyword_provider: str = Field(default="none", pattern=r"^(none|seosignal)$")
    seosignal_api_key_ref: str | None = Field(default=None, repr=False)
    # Requests a day for the whole service. The account allows 50; the rest
    # is left for using the SEO Signal panel by hand without hitting the cap.
    keyword_daily_budget: int = Field(default=40, ge=0, le=10_000)
    # Requests a day any one customer can cause, so one cannot spend everyone's.
    keyword_tenant_daily_budget: int = Field(default=10, ge=0, le=10_000)

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
    def google_sign_in_is_fully_configured(self):
        values = (self.google_oauth_client_id, self.google_oauth_client_secret, self.public_base_url)
        if any(values) and not all(values):
            raise ValueError(
                "Google sign-in needs SEO_AGENT_GOOGLE_OAUTH_CLIENT_ID, "
                "SEO_AGENT_GOOGLE_OAUTH_CLIENT_SECRET and SEO_AGENT_PUBLIC_BASE_URL together."
            )
        if self.public_base_url and not self.public_base_url.startswith("https://"):
            if self.environment.strip().lower() == "production":
                raise ValueError("SEO_AGENT_PUBLIC_BASE_URL must be https in production.")
        return self

    @model_validator(mode="after")
    def keyword_provider_is_fully_configured(self):
        if self.keyword_provider == "seosignal" and not self.seosignal_api_key_ref:
            raise ValueError(
                "SEO_AGENT_SEOSIGNAL_API_KEY_REF is required when SEO_AGENT_KEYWORD_PROVIDER is 'seosignal'."
            )
        return self

    @property
    def google_sign_in_enabled(self) -> bool:
        return bool(self.google_oauth_client_id)

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
            google_oauth_client_id=_env_optional("SEO_AGENT_GOOGLE_OAUTH_CLIENT_ID"),
            google_oauth_client_secret=_env_optional("SEO_AGENT_GOOGLE_OAUTH_CLIENT_SECRET"),
            public_base_url=_env_optional("SEO_AGENT_PUBLIC_BASE_URL"),
            keyword_provider=os.getenv("SEO_AGENT_KEYWORD_PROVIDER", "none").strip().lower() or "none",
            seosignal_api_key_ref=_env_optional("SEO_AGENT_SEOSIGNAL_API_KEY_REF"),
            keyword_daily_budget=int(os.getenv("SEO_AGENT_KEYWORD_DAILY_BUDGET", "40")),
            keyword_tenant_daily_budget=int(os.getenv("SEO_AGENT_KEYWORD_TENANT_DAILY_BUDGET", "10")),
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
