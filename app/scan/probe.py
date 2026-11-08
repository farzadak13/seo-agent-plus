"""What the site's server says about itself, apart from its pages.

A few requests a scan makes once per site, all through the guard
(app.net.guard): the http address and the other host (with or without www.)
should both lead to the one address the site uses, the home page should come
compressed and say it is https-only, and the certificate should not be about
to run out. Bounded in time like a page fetch: the worker waits for nobody.
"""
from __future__ import annotations

import ssl
import threading
from dataclasses import dataclass
from http.client import HTTPException
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request

from app.net.guard import UnsafeAddressError, open_public, peer_certificate

USER_AGENT = "HoshyarSEO-Scan/1.0 (+https://hoshyarseo.ir/)"
TIMEOUT_SECONDS = 10.0
TOTAL_SECONDS = 45.0
# A probe left behind by a server that holds it keeps its slot until the socket
# ends; this is the most of them there can be, as for page fetches.
_PROBE_SLOTS = threading.BoundedSemaphore(4)


@dataclass(frozen=True)
class SiteProbe:
    # None everywhere: not known (no answer, or not asked).
    # The host the site lives on: where its home page ends, which may not be
    # the one the customer typed (shop.ir that redirects to www.shop.ir).
    host: str = ""
    https_answers: bool | None = None
    # The http:// address ends on the https one.
    http_to_https: bool | None = None
    # The first answer of the http:// address when it redirects.
    http_redirect_code: int | None = None
    # The other host (with or without www.) and whether it leads to the site's own.
    other_host: str = ""
    other_host_redirects: bool | None = None
    compressed: bool | None = None
    hsts: bool | None = None
    certificate_days: int | None = None
    certificate_invalid: bool = False

    @property
    def known(self) -> bool:
        """Whether the server answered anything: a probe that overstayed, or got no answer, knows nothing."""
        return (
            self.https_answers is not None or self.http_to_https is not None or self.other_host_redirects is not None
            or self.compressed is not None or self.certificate_days is not None or self.certificate_invalid
        )


# The answer of a server that refused the request outright (port closed, no
# certificate for the name): it said no, which is not the same as silence.
REFUSED = -1


def _open(url: str, *, encoding: bool = False) -> tuple[int | None, str | None, dict, list]:
    """(status, final address, headers, the redirects on the way); no body is read.
    A status and no address when the server answered with an error; REFUSED
    when it refused; None when nothing came back."""
    hops: list[tuple[int, str]] = []
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}
    if encoding:
        headers["Accept-Encoding"] = "gzip, deflate, br"
    try:
        with open_public(Request(url, headers=headers), timeout=TIMEOUT_SECONDS, same_host=True, hops=hops) as answer:
            return answer.status, answer.geturl(), {k.lower(): v for k, v in answer.headers.items()}, hops
    except (UnsafeAddressError, OSError, ValueError, HTTPException) as exc:
        status = getattr(exc, "code", None)
        if isinstance(status, int):
            return status, None, {}, hops
        reason = exc.reason if isinstance(exc, URLError) else exc
        if isinstance(reason, (ConnectionRefusedError, ssl.SSLError)):
            return REFUSED, None, {}, hops
        return None, None, {}, hops


def _with(url: str, *, scheme: str | None = None, host: str | None = None) -> str:
    parts = urlsplit(url)
    netloc = host if host is not None else parts.netloc
    return urlunsplit((scheme or parts.scheme, netloc, "/", "", ""))


def _host(url: str | None) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".") if url else ""


def _hsts(headers: dict) -> bool:
    """A Strict-Transport-Security header that asks for something: max-age=0 is turning it off."""
    value = headers.get("strict-transport-security", "").lower().replace(" ", "")
    return bool(value) and "max-age=0" not in value.split(";")


def _probe(base_url: str) -> SiteProbe:
    found: dict = {}
    # The home page over https: compression and HSTS.
    status, https_final, headers, _ = _open(_with(base_url, scheme="https"), encoding=True)
    if status is not None or https_final is not None:
        found["https_answers"] = status == 200 and urlsplit(https_final or "").scheme == "https"
    if status == 200:
        found["compressed"] = any(kind in headers.get("content-encoding", "").lower() for kind in ("gzip", "br", "deflate", "zstd"))
        found["hsts"] = _hsts(headers) if found.get("https_answers") else None
    # The http address: to https, and by a permanent redirect.
    status, http_final, _, hops = _open(_with(base_url, scheme="http"))
    if http_final is not None:
        found["http_to_https"] = urlsplit(http_final).scheme == "https"
        found["http_redirect_code"] = hops[0][0] if hops else None
    # Where the site lives, not what was typed: shop.ir may rightly redirect to www.shop.ir.
    host = (found.get("https_answers") and _host(https_final)) or _host(http_final) or _host(base_url)
    found["host"] = host
    # The other host: one site, one address. Only a page counts as a second copy:
    # a bot challenge or an error page is not one.
    other = host[4:] if host.startswith("www.") else f"www.{host}"
    found["other_host"] = other
    status, final, headers, _ = _open(_with(base_url, host=other))
    if final is not None:
        if _host(final) == host:
            found["other_host_redirects"] = True
        elif status == 200 and "html" in headers.get("content-type", "").lower():
            found["other_host_redirects"] = False
    # The certificate of the host the site lives on, wherever https answers.
    if found.get("https_answers") or urlsplit(base_url).scheme == "https":
        try:
            certificate = peer_certificate(host, timeout=TIMEOUT_SECONDS)
            ends = ssl.cert_time_to_seconds(certificate["notAfter"])
            found["certificate_days"] = int((ends - datetime.now(timezone.utc).timestamp()) // 86_400)
        except ssl.SSLCertVerificationError:
            found["certificate_invalid"] = True
        except (UnsafeAddressError, OSError, ValueError, KeyError):
            pass
    return SiteProbe(**found)


def probe_site(base_url: str) -> SiteProbe:
    """The probe, in bounded time whatever the server does; empty if it overstays."""
    if not _PROBE_SLOTS.acquire(blocking=False):
        return SiteProbe()  # too many left behind already: not asked this time
    answer: list[SiteProbe] = []

    def work() -> None:
        try:
            answer.append(_probe(base_url))
        except Exception:  # anything unexpected is "not known", never a failed scan
            answer.append(SiteProbe())
        finally:
            _PROBE_SLOTS.release()

    thread = threading.Thread(target=work, daemon=True, name="scan-probe")
    thread.start()
    thread.join(TOTAL_SECONDS)
    return answer[0] if answer else SiteProbe()
