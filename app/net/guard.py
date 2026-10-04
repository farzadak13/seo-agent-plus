"""Outbound requests to addresses a customer chose.

A site's base URL is typed by the customer. Fetched without a check, it is a
way to make this server request its own loopback services, the private
network, or the cloud provider's metadata endpoint, and to read the answer
back through any endpoint that reports what the site said. Every request to a
customer-chosen host goes through ``open_public``:

* the host must resolve only to public addresses, and so must every redirect;
* the connection is made to the address that was checked. Resolving once to
  check and again to connect is exactly what DNS rebinding exploits: the
  customer controls their DNS and can answer "public" to the check and
  "127.0.0.1" a millisecond later. TLS still verifies the certificate against
  the host name, and the Host header is unchanged.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


class UnsafeAddressError(ValueError):
    """The URL points somewhere this server must not be made to request."""


def resolve_public(host: str, port: int) -> str:
    """The first address of ``host``, provided every address it has is public."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeAddressError(f"The host {host} does not resolve.") from exc
    if not infos:
        raise UnsafeAddressError(f"The host {host} does not resolve.")
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            raise UnsafeAddressError(
                f"The host {host} resolves to a non-public address and will not be fetched."
            )
    return infos[0][4][0]


def ensure_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeAddressError("Only http and https addresses can be fetched.")
    if not parsed.hostname:
        raise UnsafeAddressError("The address has no host.")
    resolve_public(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))


def same_site(first: str, second: str) -> bool:
    """Same host, treating www. as the same site."""
    def bare(url: str) -> str:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        return host[4:] if host.startswith("www.") else host
    return bool(bare(first)) and bare(first) == bare(second)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        address = resolve_public(self.host, self.port)
        self.sock = socket.create_connection((address, self.port), self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        address = resolve_public(self.host, self.port)
        raw = socket.create_connection((address, self.port), self.timeout, self.source_address)
        # Certificate checked against the name, connection made to the address.
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class _PinnedHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


class _CheckedRedirects(HTTPRedirectHandler):
    def __init__(self, origin: str | None) -> None:
        super().__init__()
        self._origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ensure_public_url(newurl)
        if self._origin is not None:
            if not same_site(self._origin, newurl):
                # An open redirect on the customer's site must not carry their
                # credentials, or our ownership check, to somebody else's host.
                raise UnsafeAddressError("The site redirected to a different host; not followed.")
            if urlparse(self._origin).scheme == "https" and urlparse(newurl).scheme != "https":
                # Credentials would then travel unencrypted.
                raise UnsafeAddressError("The site redirected from https to http; not followed.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public(request: Request, *, timeout: float, same_host: bool = False):
    """urlopen to a public address only, pinned to the address that was checked.

    ``same_host`` also refuses a redirect that leaves the original site or
    drops from https to http.
    """
    ensure_public_url(request.full_url)
    origin = request.full_url if same_host else None
    # No proxy from the environment: the check is about where the request
    # really goes, and a proxy would make that the proxy's decision.
    opener = build_opener(
        ProxyHandler({}), _PinnedHTTPHandler(), _PinnedHTTPSHandler(), _CheckedRedirects(origin)
    )
    return opener.open(request, timeout=timeout)
