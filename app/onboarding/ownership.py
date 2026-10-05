"""Proof that a tenant controls a site, before its Search Console data is theirs.

Every customer grants the same service account, so that account can read
every customer's property. Without a check, any tenant could create a site
named after a competitor's domain, pick that domain's property from the list,
and read its search data. The property list and the property connection are
therefore both limited to sites the tenant has proved they control, and to
the property of that same domain.

Two proofs, either is enough:

* **WordPress connector**: the HoshyarSEO Connector plugin answers on the
  site with an Editor-level application password, and reports the same home
  address. Only someone who runs the site can produce that.
* **Meta tag**: ``<meta name="hoshyarseo-site-verification" content="...">``
  on the home page, the same proof Google itself accepts.
"""
from __future__ import annotations

import secrets
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request

from app.models.sites import SecretProvider
from app.net.guard import UnsafeAddressError, open_public, same_site


META_NAME = "hoshyarseo-site-verification"
METHOD_CONNECTOR = "wordpress_connector"
METHOD_META_TAG = "meta_tag"
USER_AGENT = "HoshyarSEO/0.1 (+https://hoshyarseo.ir)"
DEFAULT_CONNECTOR_PREFIX = "/wp-json/hoshyarseo/v1"


def credential_is_shared(reference) -> bool:
    """An environment credential is the operator's, and so every tenant's."""
    return reference.provider == SecretProvider.ENVIRONMENT


def new_token() -> str:
    return secrets.token_urlsafe(24)


def meta_tag(token: str) -> str:
    return f'<meta name="{META_NAME}" content="{token}">'


def _bare_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def property_matches_site(property_url: str, site_url: str) -> bool:
    """Is this Search Console property the site's own, and nothing wider?

    A domain property covers every subdomain, so it matches only a site on the
    bare domain itself: proving control of shop.example.com says nothing about
    example.com's other subdomains. www. is treated as the same site.
    """
    site_host = _bare_host(site_url)
    if not site_host:
        return False
    if property_url.startswith("sc-domain:"):
        return property_url[len("sc-domain:"):].lower().rstrip(".") == site_host
    return _bare_host(property_url) == site_host


def property_covers_site(property_url: str, site_url: str) -> bool:
    """Does this property include the site's data, possibly with more?

    Looser than property_matches_site: a domain property also covers each of
    its subdomains. Right only where Google itself has said the person may
    read the property (their own sign-in), so the wider data is theirs too;
    with the shared account it would hand over a whole domain on the strength
    of one subdomain.
    """
    if property_matches_site(property_url, site_url):
        return True
    if not property_url.startswith("sc-domain:"):
        return False
    domain = property_url[len("sc-domain:"):].lower().rstrip(".")
    site_host = (urlparse(site_url).hostname or "").lower().rstrip(".")
    return bool(domain) and site_host.endswith("." + domain)


class _MetaFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        values = {key.lower(): (value or "") for key, value in attrs}
        if values.get("name", "").lower() == META_NAME:
            self.tokens.append(values.get("content", "").strip())


def find_tokens(html: str) -> list[str]:
    finder = _MetaFinder()
    finder.feed(html)
    return finder.tokens


def fetch_home_page(url: str, timeout: float = 15.0) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with open_public(request, timeout=timeout, same_host=True) as response:
        return response.read(2_000_000).decode("utf-8", errors="replace")


class OwnershipVerifier:
    def __init__(
        self,
        *,
        adapter_factory=None,
        fetch_html: Callable[[str], str] = fetch_home_page,
    ) -> None:
        self._adapter_factory = adapter_factory
        self._fetch_html = fetch_html

    def verify(self, site) -> tuple[str | None, list[str]]:
        """Return (method, reasons): the method that proved it, or None and why not."""
        reasons: list[str] = []
        site_url = str(site.base_url)
        parsed = urlparse(site_url)
        if parsed.path not in {"", "/"} or parsed.query:
            # Control of one path (a multisite sub-blog, a page on shared
            # hosting) is not control of the domain whose data it unlocks.
            return None, ["The site address must be the domain itself, like https://example.com/."]
        root = f"{parsed.scheme}://{parsed.netloc}/"

        if site.site_adapter is not None and self._adapter_factory is not None:
            method, reason = self._via_connector(site)
            if method:
                return method, []
            reasons.append(reason)

        if site.verification_token:
            try:
                tokens = find_tokens(self._fetch_html(root))
            except UnsafeAddressError as exc:
                reasons.append(str(exc))
            except Exception as exc:
                reasons.append(f"The home page could not be read: {type(exc).__name__}.")
            else:
                if any(secrets.compare_digest(token, site.verification_token) for token in tokens):
                    return METHOD_META_TAG, []
                reasons.append("The verification meta tag was not found on the home page.")
        return None, reasons

    def _via_connector(self, site) -> tuple[str | None, str]:
        site_url = str(site.base_url)
        config = site.site_adapter.config or {}
        configured = config.get("base_url")
        if configured is not None:
            parsed = urlparse(str(configured))
            # A subdomain or a path is allowed as an API host for writing,
            # but whoever holds some subdomain, or some directory on shared
            # hosting, could answer for the whole domain from there.
            if not same_site(str(configured), site_url) or parsed.path not in {"", "/"} or parsed.query:
                return None, "The connector must answer at the site's own root to prove ownership."
        if config.get("api_prefix", DEFAULT_CONNECTOR_PREFIX) != DEFAULT_CONNECTOR_PREFIX:
            # Any other prefix could point at a static file someone placed.
            return None, "The connector must use its standard address to prove ownership."
        try:
            adapter = self._adapter_factory.build(site)
        except Exception:
            return None, "The site connection is not usable."
        status_call = getattr(adapter, "status", None)
        if status_call is None:
            return None, "This kind of site connection cannot prove ownership."
        try:
            status = status_call()
        except Exception as exc:
            return None, str(exc)
        if not status.get("can_verify_ownership"):
            return None, "The WordPress user must be an Editor or Administrator to prove ownership."
        home_url = status.get("home_url", "")
        if not same_site(home_url, site_url) or urlparse(home_url).path not in {"", "/"}:
            return None, "The WordPress site reports a different address than this site."
        return METHOD_CONNECTOR, ""


def site_may_read_search_console(site) -> bool:
    """Whether a site's Search Console data may be read for it at all.

    The rule an analysis applies, shared by the warehouse sync and its status:
    with the operator's shared account, only a proven site and exactly its own
    property; with the customer's own Google grant, a property that covers the
    site.
    """
    if site.gsc is None or getattr(site.status, "value", site.status) != "active":
        return False
    if credential_is_shared(site.gsc.credential_ref):
        return site.ownership_verified_at is not None and property_matches_site(
            site.gsc.property_url, str(site.base_url)
        )
    return property_covers_site(site.gsc.property_url, str(site.base_url))
