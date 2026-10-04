"""WordPress through the HoshyarSEO Connector plugin.

The plain WordPress adapter edits ``post.title``, which is the H1 on the page
and only sometimes the <title> Google shows: Yoast, Rank Math and the other SEO
plugins build the <title> from their own templates and fields. Editing the H1
to change a search snippet changes the wrong thing.

The connector plugin (integrations/wordpress/hoshyarseo-connector) instead
sets an override that every SEO plugin's title filter honours, and keeps the
history to undo it exactly. This adapter reads the title from the public page
itself, which is the one place it is certain to be what Google sees.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.net.guard import UnsafeAddressError, open_public

from app.models.execution import ExecutionCapability
from app.models.site_adapter import (
    AdapterOperation,
    AdapterOperationResult,
    AdapterRollbackRequest,
    SitePage,
)


USER_AGENT = "HoshyarSEO/0.1 (+https://hoshyarseo.ir)"
CHANGE_PREFIX = "hoshyarseo:"


class HoshyarConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: HttpUrl
    username: str = Field(min_length=1)
    application_password: str = Field(min_length=1, repr=False)
    # A path only: a query or fragment here would let the configured prefix
    # rewrite which endpoint the credentials are sent to.
    api_prefix: str = Field(default="/wp-json/hoshyarseo/v1", pattern=r"^/[A-Za-z0-9/_.-]*$")
    timeout_seconds: float = Field(default=30.0, gt=0)


class HoshyarConnectorError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    body: bytes


Transport = Callable[[str, str, dict[str, str], bytes | None, float], HTTPResponse]


def default_transport(method, url, headers, body, timeout) -> HTTPResponse:
    """Every status comes back as a response: the error body is the explanation."""
    from urllib.request import Request

    request = Request(url=url, data=body, headers=headers, method=method)
    try:
        # The host is the customer's choice: never let it be this server's
        # own network. See app.net.guard.
        with open_public(request, timeout=timeout, same_host=True) as response:
            return HTTPResponse(response.status, response.read())
    except HTTPError as exc:
        return HTTPResponse(exc.code, exc.read())
    except URLError as exc:
        raise HoshyarConnectorError(f"Could not reach the site: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HoshyarConnectorError("The site did not answer in time.") from exc
    except UnsafeAddressError as exc:
        raise HoshyarConnectorError(str(exc)) from exc


class HoshyarConnectorAdapter:
    # Rollback restores the exact state before the change (including "no
    # override"), rather than writing the old rendered title back as a new
    # override that would then stop following the site's own template.
    exact_rollback = True

    def __init__(
        self,
        *,
        config: HoshyarConnectorConfig,
        adapter_id: str = "hoshyarseo",
        transport: Transport = default_transport,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._adapter_id = adapter_id
        self._transport = transport
        self._clock = clock
        self._api = str(config.base_url).rstrip("/") + "/" + config.api_prefix.strip("/")

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def capabilities(self) -> set[ExecutionCapability]:
        return {
            ExecutionCapability.READ_PAGE,
            ExecutionCapability.UPDATE_TITLE,
            ExecutionCapability.ROLLBACK_CHANGE,
        }

    # --- reading ------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """A connection check: only fields we know, never the raw answer."""
        data = self._call("GET", "/status")
        return {
            "plugin_version": str(data.get("plugin_version", "")),
            "seo_plugins": [str(item) for item in data.get("seo_plugins", []) if isinstance(item, str)],
            "home_url": str(data.get("home_url", "")),
            "can_verify_ownership": data.get("can_verify_ownership") is True,
        }

    def read_page(self, *, site_id: str, normalized_url: str) -> SitePage:
        info = self._call("GET", "/page?url=" + quote(normalized_url, safe=""))
        rendered = self._read_public_page(normalized_url)
        return SitePage(
            site_id=site_id,
            normalized_url=normalized_url,
            title=rendered.title,
            meta_description=rendered.meta_description,
            status=str(info.get("status", "unknown")),
            metadata={
                "post_id": info.get("post_id"),
                "post_type": info.get("post_type"),
                "post_title": info.get("post_title"),
                "override_title": info.get("override_title"),
                "seo_plugins": info.get("seo_plugins", []),
                "permalink": info.get("permalink"),
            },
        )

    def _read_public_page(self, url: str) -> "_TitleParser":
        # A query string most page caches treat as a miss, so the check sees
        # the page as it is now rather than as it was cached.
        parsed = urlparse(url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        query.append(("hoshyarseo_check", str(int(self._clock()))))
        fresh = urlunparse(parsed._replace(query=urlencode(query)))
        response = self._transport(
            "GET",
            fresh,
            {"User-Agent": USER_AGENT, "Cache-Control": "no-cache", "Accept": "text/html"},
            None,
            self._config.timeout_seconds,
        )
        if response.status_code != 200:
            raise HoshyarConnectorError(
                f"The public page returned HTTP {response.status_code}.",
                status_code=response.status_code,
            )
        parser = _TitleParser()
        parser.feed(response.body.decode("utf-8", errors="replace"))
        if parser.title is None:
            raise HoshyarConnectorError("The public page has no <title>.")
        return parser

    # --- writing ------------------------------------------------------------

    def update_title(
        self, *, site_id: str, normalized_url: str, new_title: str, idempotency_key: str
    ) -> AdapterOperationResult:
        change = self._call(
            "POST",
            "/title",
            {"url": normalized_url, "title": new_title, "idempotency_key": idempotency_key},
        )
        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_TITLE,
            site_id=site_id,
            normalized_url=normalized_url,
            message="Title override applied.",
            change_id=CHANGE_PREFIX + str(change["change_id"]),
            previous_value=change.get("previous"),
            new_value=change.get("new"),
            metadata={"post_id": change.get("post_id"), "purged": change.get("purged", [])},
        )

    def rollback_change(self, *, request: AdapterRollbackRequest) -> AdapterOperationResult:
        if not request.change_id.startswith(CHANGE_PREFIX):
            raise HoshyarConnectorError("Not a HoshyarSEO change id.")
        try:
            change = self._call(
                "POST",
                "/rollback",
                {
                    "url": request.normalized_url,
                    "change_id": request.change_id.removeprefix(CHANGE_PREFIX),
                },
            )
        except HoshyarConnectorError as exc:
            if exc.status_code == 409:
                return AdapterOperationResult(
                    success=False,
                    operation=AdapterOperation.ROLLBACK_CHANGE,
                    site_id=request.site_id,
                    normalized_url=request.normalized_url,
                    message=str(exc),
                    change_id=request.change_id,
                )
            raise
        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.ROLLBACK_CHANGE,
            site_id=request.site_id,
            normalized_url=request.normalized_url,
            message="Title override rolled back.",
            change_id=request.change_id,
            metadata={"post_id": change.get("post_id"), "purged": change.get("purged", [])},
        )

    # --- not offered by the connector ---------------------------------------

    def _unsupported(self, *args, **kwargs):
        raise HoshyarConnectorError("The HoshyarSEO connector only changes titles.")

    update_meta_description = _unsupported
    update_content = _unsupported
    update_internal_links = _unsupported
    publish_content = _unsupported

    # --- transport ----------------------------------------------------------

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
        token = base64.b64encode(
            f"{self._config.username}:{self._config.application_password}".encode("utf-8")
        ).decode("ascii")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {token}",
            "User-Agent": USER_AGENT,
        }
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        response = self._transport(
            method, self._api + path, headers, body, self._config.timeout_seconds
        )
        try:
            data = json.loads(response.body.decode("utf-8")) if response.body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = None
        if not 200 <= response.status_code < 300:
            raise HoshyarConnectorError(
                _explain(response.status_code, data), status_code=response.status_code
            )
        if not isinstance(data, dict):
            raise HoshyarConnectorError("The connector did not return a JSON object.")
        return data


def _explain(status_code: int, data: Any) -> str:
    message = data.get("message") if isinstance(data, dict) else None
    if status_code == 404 and isinstance(data, dict) and data.get("code") == "rest_no_route":
        return "The HoshyarSEO Connector plugin is not installed or not active on this site."
    if status_code == 401:
        return "WordPress rejected the username or application password."
    if status_code == 403:
        return "This WordPress user is not allowed to edit that page."
    return f"Connector returned HTTP {status_code}: {message or 'no details'}"


class _TitleParser(HTMLParser):
    """The document's <title> and meta description; <title> inside <svg> is not it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.meta_description = ""
        self._in_title = False
        self._svg_depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "svg":
            self._svg_depth += 1
        elif tag == "title" and self._svg_depth == 0 and self.title is None:
            self._in_title = True
            self._buffer = []
        elif tag == "meta":
            values = dict(attrs)
            if (values.get("name") or "").lower() == "description" and not self.meta_description:
                self.meta_description = unescape(values.get("content") or "").strip()

    def handle_endtag(self, tag):
        if tag == "svg" and self._svg_depth:
            self._svg_depth -= 1
        elif tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._buffer).split())

    def handle_data(self, data):
        if self._in_title:
            self._buffer.append(data)
