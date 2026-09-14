from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from app.execution.site_adapter import SiteAdapter
from app.models.execution import ExecutionCapability
from app.models.rest_adapter import (
    RESTAdapterConfig,
    RESTEndpointConfig,
)
from app.models.site_adapter import (
    AdapterOperation,
    AdapterOperationResult,
    AdapterRollbackRequest,
    SitePage,
)


class RESTAdapterError(RuntimeError):
    """
    Error raised when a Generic REST Adapter operation fails.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)

        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]


HTTPTransport = Callable[
    [str, str, dict[str, str], bytes | None, float],
    HTTPResponse,
]


def default_http_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> HTTPResponse:
    """
    Standard-library HTTP transport.

    The transport is deliberately isolated so tests and future infrastructure
    can inject another HTTP client without changing the adapter contract.
    """

    request = Request(
        url=url,
        data=body,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urlopen(
            request,
            timeout=timeout,
        ) as response:
            return HTTPResponse(
                status_code=response.status,
                body=response.read(),
                headers={
                    str(key): str(value)
                    for key, value in response.headers.items()
                },
            )

    except HTTPError as exc:
        response_body = exc.read()

        retryable = exc.code in {
            408,
            425,
            429,
            500,
            502,
            503,
            504,
        }

        raise RESTAdapterError(
            f"REST request failed with HTTP {exc.code}",
            status_code=exc.code,
            retryable=retryable,
        ) from exc

    except URLError as exc:
        raise RESTAdapterError(
            f"REST transport failed: {exc.reason}",
            retryable=True,
        ) from exc


class GenericRESTSiteAdapter(SiteAdapter):
    """
    Generic HTTP/REST implementation of the SiteAdapter contract.

    No CMS-specific assumptions live here.
    """

    def __init__(
        self,
        *,
        adapter_id: str,
        config: RESTAdapterConfig,
        transport: HTTPTransport = default_http_transport,
    ) -> None:
        if not adapter_id.strip():
            raise ValueError(
                "adapter_id must not be empty"
            )

        self._adapter_id = adapter_id
        self._config = config
        self._transport = transport

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def capabilities(
        self,
    ) -> set[ExecutionCapability]:
        return {
            ExecutionCapability.READ_PAGE,
            ExecutionCapability.UPDATE_TITLE,
            ExecutionCapability.UPDATE_META_DESCRIPTION,
            ExecutionCapability.UPDATE_CONTENT,
            ExecutionCapability.UPDATE_INTERNAL_LINKS,
            ExecutionCapability.PUBLISH_CONTENT,
            ExecutionCapability.ROLLBACK_CHANGE,
        }

    def read_page(
        self,
        *,
        site_id: str,
        normalized_url: str,
    ) -> SitePage:
        endpoint = self._config.read_page

        response = self._request(
            endpoint=endpoint,
            normalized_url=normalized_url,
        )

        payload = self._decode_json(response)

        if not isinstance(payload, dict):
            raise RESTAdapterError(
                "REST read_page response must be a JSON object."
            )

        return SitePage(
            site_id=site_id,
            normalized_url=normalized_url,
            title=str(
                payload.get(
                    self._config.title_field,
                    "",
                )
            ),
            meta_description=str(
                payload.get(
                    self._config.meta_description_field,
                    "",
                )
            ),
            content=str(
                payload.get(
                    self._config.content_field,
                    "",
                )
            ),
            status=str(
                payload.get(
                    self._config.page_status_field,
                    "unknown",
                )
            ),
            metadata=payload,
        )

    def update_title(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_title: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        return self._mutate_field(
            operation=AdapterOperation.UPDATE_TITLE,
            endpoint=self._config.update_title,
            site_id=site_id,
            normalized_url=normalized_url,
            field=self._config.title_field,
            value=new_title,
            idempotency_key=idempotency_key,
        )

    def update_meta_description(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_meta_description: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        return self._mutate_field(
            operation=AdapterOperation.UPDATE_META_DESCRIPTION,
            endpoint=self._config.update_meta_description,
            site_id=site_id,
            normalized_url=normalized_url,
            field=self._config.meta_description_field,
            value=new_meta_description,
            idempotency_key=idempotency_key,
        )

    def update_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        return self._mutate_field(
            operation=AdapterOperation.UPDATE_CONTENT,
            endpoint=self._config.update_content,
            site_id=site_id,
            normalized_url=normalized_url,
            field=self._config.content_field,
            value=new_content,
            idempotency_key=idempotency_key,
        )

    def update_internal_links(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        return self._mutate_field(
            operation=AdapterOperation.UPDATE_INTERNAL_LINKS,
            endpoint=self._config.update_internal_links,
            site_id=site_id,
            normalized_url=normalized_url,
            field=self._config.content_field,
            value=new_content,
            idempotency_key=idempotency_key,
        )

    def publish_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        endpoint = self._config.publish_content

        payload = {
            "site_id": site_id,
            "url": normalized_url,
        }

        payload.update(
            self._config.request_body_extra
        )

        return self._request_mutation(
            operation=AdapterOperation.PUBLISH_CONTENT,
            endpoint=endpoint,
            site_id=site_id,
            normalized_url=normalized_url,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def rollback_change(
        self,
        *,
        request: AdapterRollbackRequest,
    ) -> AdapterOperationResult:
        endpoint = self._config.rollback_change

        path = endpoint.path.replace(
            "{change_id}",
            quote(
                request.change_id,
                safe="",
            ),
        )

        resolved_endpoint = RESTEndpointConfig(
            path=path,
            method=endpoint.method,
        )

        payload = {
            "site_id": request.site_id,
            "url": request.normalized_url,
            "change_id": request.change_id,
        }

        return self._request_mutation(
            operation=AdapterOperation.ROLLBACK_CHANGE,
            endpoint=resolved_endpoint,
            site_id=request.site_id,
            normalized_url=request.normalized_url,
            payload=payload,
            idempotency_key=request.idempotency_key,
        )

    def _mutate_field(
        self,
        *,
        operation: AdapterOperation,
        endpoint: RESTEndpointConfig,
        site_id: str,
        normalized_url: str,
        field: str,
        value: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        payload = {
            "site_id": site_id,
            "url": normalized_url,
            field: value,
        }

        payload.update(
            self._config.request_body_extra
        )

        return self._request_mutation(
            operation=operation,
            endpoint=endpoint,
            site_id=site_id,
            normalized_url=normalized_url,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def _request_mutation(
        self,
        *,
        operation: AdapterOperation,
        endpoint: RESTEndpointConfig,
        site_id: str,
        normalized_url: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> AdapterOperationResult:
        if not idempotency_key.strip():
            raise ValueError(
                "idempotency_key must not be empty"
            )

        response = self._request(
            endpoint=endpoint,
            normalized_url=normalized_url,
            payload=payload,
            idempotency_key=idempotency_key,
        )

        response_payload = self._decode_json(
            response,
            allow_empty=True,
        )

        if not isinstance(response_payload, dict):
            response_payload = {}

        change_id = (
            response_payload.get("change_id")
            or response_payload.get("id")
        )

        previous_value = response_payload.get(
            "previous_value"
        )

        new_value = response_payload.get(
            "new_value"
        )

        return AdapterOperationResult(
            success=True,
            operation=operation,
            site_id=site_id,
            normalized_url=normalized_url,
            message=str(
                response_payload.get(
                    "message",
                    "",
                )
            ),
            change_id=(
                str(change_id)
                if change_id is not None
                else None
            ),
            previous_value=previous_value,
            new_value=new_value,
            metadata=response_payload,
        )

    def _request(
        self,
        *,
        endpoint: RESTEndpointConfig,
        normalized_url: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> HTTPResponse:
        path = endpoint.path.replace(
            "{url}",
            quote(
                normalized_url,
                safe="",
            ),
        )

        url = urljoin(
            str(self._config.base_url).rstrip("/") + "/",
            path.lstrip("/"),
        )

        headers = {
            "Accept": "application/json",
            **self._config.extra_headers,
        }

        if self._config.authorization_token:
            headers["Authorization"] = (
                f"Bearer {self._config.authorization_token}"
            )

        body: bytes | None = None

        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
            ).encode("utf-8")

            headers["Content-Type"] = (
                "application/json"
            )

        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        response = self._transport(
            endpoint.method.upper(),
            url,
            headers,
            body,
            self._config.timeout_seconds,
        )

        if response.status_code < 200 or response.status_code >= 300:
            raise RESTAdapterError(
                (
                    "REST request returned unexpected "
                    f"status {response.status_code}"
                ),
                status_code=response.status_code,
                retryable=response.status_code
                in {
                    408,
                    425,
                    429,
                    500,
                    502,
                    503,
                    504,
                },
            )

        return response

    @staticmethod
    def _decode_json(
        response: HTTPResponse,
        *,
        allow_empty: bool = False,
    ) -> Any:
        if not response.body:
            if allow_empty:
                return {}

            raise RESTAdapterError(
                "REST response body is empty."
            )

        try:
            return json.loads(
                response.body.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise RESTAdapterError(
                "REST response is not valid UTF-8 JSON."
            ) from exc