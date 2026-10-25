"""Any website: the customer applies the change, HoshyarSEO checks it.

Most sites are not WordPress, and many have no API anyone could write a
title through (a hand-built site, a Next.js app, a site builder). For those
the connection is ``manual``: proposals and approvals work as everywhere,
but the customer puts the approved title in place themselves and says so,
and this adapter reads the public page to see that Google will find it there.
It never writes, so it holds no credentials.

The page is read the same way the connector adapter reads it, through the
guard that refuses this server's own network (app.net.guard), with a query
string most page caches treat as a miss.
"""
from __future__ import annotations

import time
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.execution import ExecutionCapability
from app.models.site_adapter import SitePage
from app.site_adapters.hoshyarseo import (
    USER_AGENT,
    HoshyarConnectorError,
    HTTPResponse,
    Transport,
    _TitleParser,
    default_transport,
)


class ManualAdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: HttpUrl
    timeout_seconds: float = Field(default=30.0, gt=0)


class ManualSiteAdapter:
    # The executor and rollback handler ask the customer instead of writing.
    manual_apply = True
    exact_rollback = False

    def __init__(
        self,
        *,
        config: ManualAdapterConfig,
        adapter_id: str = "manual",
        transport: Transport = default_transport,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._adapter_id = adapter_id
        self._transport = transport
        self._clock = clock

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def capabilities(self) -> set[ExecutionCapability]:
        return {ExecutionCapability.READ_PAGE}

    def read_page(self, *, site_id: str, normalized_url: str) -> SitePage:
        parsed = urlparse(normalized_url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        query.append(("hoshyarseo_check", str(int(self._clock()))))
        fresh = urlunparse(parsed._replace(query=urlencode(query)))
        response: HTTPResponse = self._transport(
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
        return SitePage(
            site_id=site_id,
            normalized_url=normalized_url,
            title=parser.title,
            meta_description=parser.meta_description,
            status="published",
            metadata={"manual": True},
        )
