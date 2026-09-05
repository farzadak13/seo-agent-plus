from __future__ import annotations

import posixpath
import re
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)


TRACKING_QUERY_PARAM_EXACT = {
    "gclid",
    "fbclid",
    "dclid",
    "msclkid",
    "yclid",
    "mc_cid",
    "mc_eid",
}

MULTI_SLASH_RE = re.compile(r"/{2,}")


def _is_tracking_parameter(name: str) -> bool:
    """
    Return True for common tracking parameters.

    We deliberately support:
    - exact names such as gclid/fbclid
    - utm_* family

    We do NOT remove arbitrary query parameters because they may
    affect the actual page/resource returned by the website.
    """
    normalized_name = name.strip().lower()

    return (
        normalized_name in TRACKING_QUERY_PARAM_EXACT
        or normalized_name.startswith("utm_")
    )


def _normalize_path(path: str) -> str:
    """
    Normalize URL path without changing trailing-slash semantics.
    """
    if not path:
        return ""

    # Collapse duplicate slashes.
    path = MULTI_SLASH_RE.sub("/", path)

    # Normalize dot segments such as /a/../b.
    normalized = posixpath.normpath(path)

    # posixpath.normpath("/") is fine, but it may remove a leading slash
    # for some relative-looking inputs.
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"

    # Preserve the meaningful root path exactly.
    if path == "/":
        return "/"

    # Preserve an originally present trailing slash.
    if path.endswith("/") and normalized != "/":
        normalized += "/"

    return normalized


def canonicalize_url(url: str) -> str:
    """
    Return a stable canonical representation for an input URL.

    Current policy:
    - require an absolute HTTP(S) URL
    - force scheme to https
    - lowercase hostname
    - preserve www
    - remove default ports
    - remove fragment
    - remove common tracking query parameters
    - sort remaining query parameters
    - preserve trailing-slash semantics
    - preserve non-tracking query parameters
    """

    if not isinstance(url, str):
        raise TypeError("url must be a string")

    value = url.strip()

    if not value:
        raise ValueError("url must not be empty")

    parsed = urlsplit(value)

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("url must use http or https")

    if not parsed.netloc:
        raise ValueError("url must contain a hostname")

    hostname = parsed.hostname

    if not hostname:
        raise ValueError("url must contain a valid hostname")

    # Accessing parsed.port can raise ValueError for malformed ports.
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("url contains an invalid port") from exc

    # IPv6 hostnames need brackets when reconstructed.
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname.lower()}]"
    else:
        hostname = hostname.lower()

    # Preserve non-default ports.
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"

    # Keep only non-tracking query parameters.
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
        if not _is_tracking_parameter(key)
    ]

    # Stable ordering for identity/deduplication.
    query_pairs.sort(key=lambda pair: (pair[0], pair[1]))

    normalized_query = urlencode(
        query_pairs,
        doseq=True,
    )

    normalized_path = _normalize_path(parsed.path)

    return urlunsplit(
        (
            "https",
            hostname,
            normalized_path,
            normalized_query,
            "",
        )
    )