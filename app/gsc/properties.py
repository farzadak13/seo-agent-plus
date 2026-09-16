"""The properties a credential can actually read, straight from Google.

This exists to remove the worst step in onboarding. Today a customer types
their property URL, and that string has to match what Google stored **exactly**
— one trailing slash out of place, ``http`` where Google has ``https``, or a
domain property typed as a URL, and every later request comes back 403. A 403
reads as "you don't have permission", so the customer goes and re-grants access
they already had, and it still fails.

Google will tell us. ``GET /webmasters/v3/sites`` returns every property the
credential can see, spelled the way Google spells it. The customer picks from
that list and nothing is typed at all.

Two things the list will not tell us, both of which have bitten people:

**Being listed is not being readable.** A ``siteUnverifiedUser`` entry appears
in the response and then refuses search analytics. Offering it as a choice
moves the 403 from onboarding to the first run, which is worse.

**With a shared service account the list is not this customer's.** Every
customer grants the same email, so the response contains properties belonging
to everyone who ever granted it. Showing that list to a customer would leak the
existence of other customers' domains. Callers must narrow it to what the
tenant is entitled to before it reaches a screen — see ``isolated_per_tenant``
in ``app.gsc.credentials``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

SITES_PATH = "/webmasters/v3/sites"

DOMAIN_PREFIX = "sc-domain:"

# Google's documented permission levels. Only the unverified one cannot read
# search analytics; "restricted" is a view-only role, which is all we need.
PERMISSION_LEVELS = {
    "siteOwner",
    "siteFullUser",
    "siteRestrictedUser",
    "siteUnverifiedUser",
}
UNREADABLE_PERMISSIONS = {"siteUnverifiedUser"}


@dataclass(frozen=True)
class GSCProperty:
    """One Search Console property, spelled exactly as Google spells it."""

    site_url: str
    permission_level: str

    @property
    def is_domain_property(self) -> bool:
        return self.site_url.startswith(DOMAIN_PREFIX)

    @property
    def readable(self) -> bool:
        """False when picking this would produce a 403 on the first run."""
        return self.permission_level not in UNREADABLE_PERMISSIONS

    @property
    def display_name(self) -> str:
        """What a human recognises; never what we send back to Google."""
        if self.is_domain_property:
            return self.site_url[len(DOMAIN_PREFIX):]
        return self.site_url

    def matches(self, candidate: str) -> bool:
        """Exact match only.

        Tempting to be forgiving here and accept a missing trailing slash.
        That is the bug: our idea of an equivalent URL is not Google's, and a
        near-miss stored now is a 403 later with nothing to point at.
        """
        return candidate == self.site_url


def parse_properties(payload: Any) -> list[GSCProperty]:
    """Read the ``sites.list`` body, skipping entries we cannot use."""
    if not isinstance(payload, dict):
        raise ValueError("The Search Console property list must be an object.")
    entries = payload.get("siteEntry", [])
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise ValueError("The Search Console property list must be a list.")

    properties: list[GSCProperty] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        site_url = entry.get("siteUrl")
        permission = entry.get("permissionLevel")
        if not isinstance(site_url, str) or not site_url:
            continue
        if not isinstance(permission, str) or not permission:
            permission = "siteUnverifiedUser"
        properties.append(GSCProperty(site_url=site_url, permission_level=permission))

    # Readable first, then alphabetically, so a picker shows usable choices at
    # the top without the caller having to know which are usable.
    properties.sort(key=lambda item: (not item.readable, item.display_name))
    return properties


def list_properties(
    *,
    request_fn: Callable[..., Any],
    token_provider=None,
    timeout_seconds: float = 30.0,
) -> list[GSCProperty]:
    """Ask Google which properties this credential can see."""
    from app.gsc.client import GSCClientError

    headers = {"Accept": "application/json"}
    if token_provider is not None:
        headers["Authorization"] = f"Bearer {token_provider.token()}"

    response = request_fn("GET", SITES_PATH, headers=headers, json=None, timeout=timeout_seconds)

    status_code = getattr(response, "status_code", 200)
    if status_code >= 400:
        classify = getattr(response, "classify", None)
        if callable(classify):
            kind = classify()
            detail = getattr(response, "diagnostic", lambda: f"HTTP {status_code}")()
            raise GSCClientError(
                f"Could not list Search Console properties: {detail}",
                retryable=kind.retryable,
                status_code=status_code,
            )
        raise GSCClientError(
            f"Could not list Search Console properties: HTTP {status_code}",
            retryable=status_code in {429, 500, 502, 503, 504},
            status_code=status_code,
        )

    try:
        return parse_properties(response.json())
    except ValueError as exc:
        raise GSCClientError(f"Unreadable Search Console property list: {exc}") from exc


__all__ = [
    "DOMAIN_PREFIX",
    "GSCProperty",
    "PERMISSION_LEVELS",
    "SITES_PATH",
    "UNREADABLE_PERMISSIONS",
    "list_properties",
    "parse_properties",
]
