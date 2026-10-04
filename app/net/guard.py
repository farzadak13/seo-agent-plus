"""Outbound requests to addresses a customer chose.

A site's base URL is typed by the customer. Fetched without a check, it is a
way to make this server request its own loopback services, the private
network, or the cloud provider's metadata endpoint, and to read the answer
back through any endpoint that reports what the site said. Every request to a
customer-chosen host goes through ``open_public``: the host must resolve only
to public addresses, and so must every redirect along the way.

DNS can change between this check and the connection (rebinding). Closing
that fully needs connecting to the checked address itself; this closes the
direct cases, which are the ones a form field makes easy.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class UnsafeAddressError(ValueError):
    """The URL points somewhere this server must not be made to request."""


def ensure_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeAddressError("Only http and https addresses can be fetched.")
    host = parsed.hostname
    if not host:
        raise UnsafeAddressError("The address has no host.")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeAddressError(f"The host {host} does not resolve.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            raise UnsafeAddressError(
                f"The host {host} resolves to a non-public address and will not be fetched."
            )


def same_site(first: str, second: str) -> bool:
    """Same host, treating www. as the same site."""
    def bare(url: str) -> str:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        return host[4:] if host.startswith("www.") else host
    return bool(bare(first)) and bare(first) == bare(second)


class _CheckedRedirects(HTTPRedirectHandler):
    def __init__(self, origin: str | None) -> None:
        super().__init__()
        self._origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ensure_public_url(newurl)
        if self._origin is not None and not same_site(self._origin, newurl):
            # An open redirect on the customer's site must not carry their
            # credentials, or our ownership check, to somebody else's host.
            raise UnsafeAddressError("The site redirected to a different host; not followed.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public(request: Request, *, timeout: float, same_host: bool = False):
    """urlopen, refusing non-public hosts at the start and at every redirect.

    ``same_host`` also refuses a redirect that leaves the original site.
    """
    ensure_public_url(request.full_url)
    origin = request.full_url if same_host else None
    return build_opener(_CheckedRedirects(origin)).open(request, timeout=timeout)
