"""CMS-specific construction stays outside the decision engine."""
from app.models.execution import ExecutionCapability
from app.models.rest_adapter import RESTAdapterConfig
from app.models.wordpress_adapter import WordPressAdapterConfig
from app.site_adapters.generic_rest import GenericRESTSiteAdapter
from app.site_adapters.hoshyarseo import HoshyarConnectorAdapter, HoshyarConnectorConfig
from app.site_adapters.wordpress import WordPressSiteAdapter


class CapabilityLimitedAdapter:
    def __init__(self, adapter, capabilities):
        if not capabilities <= adapter.capabilities:
            raise ValueError("Configured capability is not implemented by this adapter.")
        self._adapter = adapter
        self._capabilities = frozenset(capabilities)

    @property
    def adapter_id(self):
        return self._adapter.adapter_id

    @property
    def capabilities(self):
        return set(self._capabilities)

    def __getattr__(self, name):
        return getattr(self._adapter, name)


class SiteAdapterFactory:
    def __init__(self, secret_resolver):
        self.secret_resolver = secret_resolver
        self._builders = {}
        self.register("generic_rest", RESTAdapterConfig, GenericRESTSiteAdapter,
                      secret_fields={"authorization_token"})
        self.register("wordpress", WordPressAdapterConfig, WordPressSiteAdapter,
                      secret_fields={"username", "application_password"})
        # WordPress with the HoshyarSEO Connector plugin: changes the <title>
        # Google shows, not the post's H1. The one to use for customers.
        self.register("hoshyarseo", HoshyarConnectorConfig, HoshyarConnectorAdapter,
                      secret_fields={"username", "application_password"})

    def register(self, adapter_type, config_model, builder, *, secret_fields=()):
        if adapter_type in self._builders:
            raise ValueError("Adapter type already registered.")
        self._builders[adapter_type] = (config_model, builder, set(secret_fields))

    def build(self, site):
        connection = site.site_adapter
        if connection is None:
            raise ValueError("Site adapter is not configured.")
        try:
            model, builder, secret_fields = self._builders[connection.adapter_type]
        except KeyError:
            raise ValueError("Unsupported adapter type; register its builder first.") from None
        values = dict(connection.config)
        enabled = values.pop("enabled_capabilities", None)
        if enabled is not None and not isinstance(enabled, list):
            raise ValueError("enabled_capabilities must be a list.")
        if secret_fields.intersection(values):
            raise ValueError("Credentials must use secret_refs, not config.")
        if set(connection.secret_refs) - secret_fields:
            raise ValueError("Unknown credential field for this adapter.")
        # Keep arbitrary credential-bearing headers out of persisted connection config.
        if any(k.lower() in {"authorization", "proxy-authorization", "cookie", "x-api-key"}
               for k in values.get("extra_headers", {})):
            raise ValueError("Credential headers must not be stored in config.")
        values.setdefault("base_url", str(site.base_url))
        for field, reference in connection.secret_refs.items():
            values[field] = self.secret_resolver.resolve(reference)
        adapter = builder(adapter_id=f"{connection.adapter_type}:{site.site_id}", config=model.model_validate(values))
        if enabled is not None:
            adapter = CapabilityLimitedAdapter(adapter, {ExecutionCapability(value) for value in enabled})
        return adapter
