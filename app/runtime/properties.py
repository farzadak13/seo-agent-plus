"""Building the property lister the API uses during onboarding.

Kept out of the API layer on purpose: the API should not know what a token
provider or an egress proxy is. It asks a callable "what can this credential
see", and the composition root decides how that question reaches Google.
"""
from __future__ import annotations

from app.gsc.credentials import build_token_provider
from app.gsc.properties import list_properties
from app.models.sites import SecretProvider, SecretRef


def google_client_options(settings) -> dict:
    """client_id/secret for refreshing customers' Google sign-ins, when configured."""
    if not getattr(settings, "google_oauth_client_id", None):
        return {}
    return {
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
    }


def build_property_lister(settings, *, secret_resolver, transport):
    """Return ``(auth_mode, credential_ref) -> [GSCProperty]``.

    The credential is resolved per call rather than held, so rotating a secret
    takes effect without a restart.
    """

    def lister(*, auth_mode: str, credential_ref):
        # A string names an operator credential in the environment; a
        # SecretRef is a customer's own Google sign-in, held in the vault.
        reference = (
            credential_ref
            if isinstance(credential_ref, SecretRef)
            else SecretRef(provider=SecretProvider.ENVIRONMENT, key=credential_ref)
        )
        secret = secret_resolver.resolve(reference)
        provider = build_token_provider(
            kind=auth_mode,
            secret=secret,
            session=getattr(transport, "session", None),
            **google_client_options(settings),
        )
        return list_properties(
            request_fn=transport,
            token_provider=provider,
            timeout_seconds=settings.gsc_timeout_seconds,
        )

    return lister


__all__ = ["build_property_lister"]
