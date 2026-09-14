from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse

from app.execution.site_adapter import SiteAdapter
from app.models.execution import ExecutionCapability
from app.models.site_adapter import (
    AdapterOperation,
    AdapterOperationResult,
    AdapterRollbackRequest,
    SitePage,
)
from app.models.wordpress_adapter import (
    WordPressAdapterConfig,
    WordPressContentType,
)


class WordPressAdapterError(RuntimeError):
    """
    Error raised by the WordPress REST adapter.
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
class WordPressHTTPResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]


WordPressTransport = Callable[
    [
        str,
        str,
        dict[str, str],
        bytes | None,
        float,
    ],
    WordPressHTTPResponse,
]


def default_wordpress_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> WordPressHTTPResponse:
    """
    Standard-library HTTP transport for WordPress.

    Keeping transport injectable makes authentication, HTTP behavior,
    and failure handling independently testable.
    """

    from urllib.request import Request, urlopen

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
            return WordPressHTTPResponse(
                status_code=response.status,
                body=response.read(),
                headers={
                    str(key): str(value)
                    for key, value in response.headers.items()
                },
            )

    except HTTPError as exc:
        exc.read()

        retryable = exc.code in {
            408,
            425,
            429,
            500,
            502,
            503,
            504,
        }

        raise WordPressAdapterError(
            f"WordPress REST request failed with HTTP {exc.code}",
            status_code=exc.code,
            retryable=retryable,
        ) from exc

    except URLError as exc:
        raise WordPressAdapterError(
            f"WordPress transport failed: {exc.reason}",
            retryable=True,
        ) from exc


class WordPressSiteAdapter(SiteAdapter):
    """
    Real WordPress REST API implementation.

    This adapter is infrastructure-only:
    SEO decisions, scoring, strategy and action planning stay outside it.
    """

    def __init__(
        self,
        *,
        config: WordPressAdapterConfig,
        adapter_id: str = "wordpress",
        transport: WordPressTransport = default_wordpress_transport,
    ) -> None:
        if not adapter_id.strip():
            raise ValueError("adapter_id must not be empty")
        self._adapter_id = adapter_id
        self._config = config
        self._transport = transport

        self._base_api_url = urljoin(
            str(config.base_url).rstrip("/") + "/",
            config.api_prefix.strip("/") + "/",
        )

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
        post = self._resolve_post(
            normalized_url=normalized_url,
        )

        return self._to_site_page(
            site_id=site_id,
            normalized_url=normalized_url,
            post=post,
        )

    def update_title(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_title: str,
        idempotency_key: str,
) -> AdapterOperationResult:
        if not idempotency_key.strip():
            raise ValueError(
                "idempotency_key must not be empty"
            )

        post = self._resolve_post(
            normalized_url=normalized_url,
        )

        previous_title = self._raw_value(
            post.get("title")
        )

        updated = self._update_post(
            post_id=self._post_id(post),
            payload={
                "title": new_title,
            },
            idempotency_key=idempotency_key,
        )

        change_id = self._build_change_id(
            post=updated,
        )

        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_TITLE,
            site_id=site_id,
            normalized_url=normalized_url,
            message="WordPress title updated.",
            change_id=change_id,
            previous_value=previous_title,
            new_value=self._raw_value(
                updated.get("title")
            ),
            metadata={
                "post_id": self._post_id(updated),
                "content_type": self._config.content_type.value,
            },
        )

    def update_meta_description(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_meta_description: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        """
        WordPress core does not expose a universal meta-description field.

        Therefore this operation uses a REST-visible meta field through
        the `meta` object. The actual field must be registered by the
        site's WordPress configuration/plugin.
        """

        post = self._resolve_post(
            normalized_url=normalized_url,
        )

        post_id = self._post_id(post)

        previous = self._read_meta_description(
            post
        )

        updated = self._update_post(
            post_id=post_id,
            payload={
                "meta": {
                    "_seo_agent_meta_description":
                        new_meta_description,
                }
            },
            idempotency_key=idempotency_key,
        )

        return AdapterOperationResult(
            success=True,
            operation=(
                AdapterOperation.UPDATE_META_DESCRIPTION
            ),
            site_id=site_id,
            normalized_url=normalized_url,
            message=(
                "WordPress meta description updated "
                "through the registered meta field."
            ),
            change_id=self._build_change_id(
                post=updated,
            ),
            previous_value=previous,
            new_value=self._read_meta_description(
                updated
            ),
            metadata={
                "post_id": post_id,
                "meta_key": "_seo_agent_meta_description",
            },
        )

    def update_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        post = self._resolve_post(
            normalized_url=normalized_url,
        )

        previous = self._raw_value(
            post.get("content")
        )

        updated = self._update_post(
            post_id=self._post_id(post),
            payload={
                "content": new_content,
            },
            idempotency_key=idempotency_key,
        )

        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.UPDATE_CONTENT,
            site_id=site_id,
            normalized_url=normalized_url,
            message="WordPress content updated.",
            change_id=self._build_change_id(
                post=updated,
            ),
            previous_value=previous,
            new_value=self._raw_value(
                updated.get("content")
            ),
            metadata={
                "post_id": self._post_id(updated),
            },
        )

    def update_internal_links(
        self,
        *,
        site_id: str,
        normalized_url: str,
        new_content: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        post = self._resolve_post(
            normalized_url=normalized_url,
        )

        previous = self._raw_value(
            post.get("content")
        )

        updated = self._update_post(
            post_id=self._post_id(post),
            payload={
                "content": new_content,
            },
            idempotency_key=idempotency_key,
        )

        return AdapterOperationResult(
            success=True,
            operation=(
                AdapterOperation.UPDATE_INTERNAL_LINKS
            ),
            site_id=site_id,
            normalized_url=normalized_url,
            message=(
                "WordPress content updated for internal-link "
                "optimization."
            ),
            change_id=self._build_change_id(
                post=updated,
            ),
            previous_value=previous,
            new_value=self._raw_value(
                updated.get("content")
            ),
            metadata={
                "post_id": self._post_id(updated),
            },
        )

    def publish_content(
        self,
        *,
        site_id: str,
        normalized_url: str,
        idempotency_key: str,
    ) -> AdapterOperationResult:
        post = self._resolve_post(
            normalized_url=normalized_url,
        )

        updated = self._update_post(
            post_id=self._post_id(post),
            payload={
                "status": "publish",
            },
            idempotency_key=idempotency_key,
        )

        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.PUBLISH_CONTENT,
            site_id=site_id,
            normalized_url=normalized_url,
            message="WordPress content published.",
            change_id=self._build_change_id(
                post=updated,
            ),
            metadata={
                "post_id": self._post_id(updated),
                "status": updated.get("status"),
            },
        )

    def rollback_change(
        self,
        *,
        request: AdapterRollbackRequest,
    ) -> AdapterOperationResult:
        change = self._parse_change_id(
            request.change_id
        )

        revision = self._get_revision(
            post_id=change["post_id"],
            revision_id=change["revision_id"],
        )

        payload: dict[str, Any] = {}

        title = revision.get("title")
        if isinstance(title, dict):
            title_raw = title.get("raw")

            if title_raw is not None:
                payload["title"] = title_raw

        content = revision.get("content")
        if isinstance(content, dict):
            content_raw = content.get("raw")

            if content_raw is not None:
                payload["content"] = content_raw

        excerpt = revision.get("excerpt")
        if isinstance(excerpt, dict):
            excerpt_raw = excerpt.get("raw")

            if excerpt_raw is not None:
                payload["excerpt"] = excerpt_raw

        if not payload:
            raise WordPressAdapterError(
                "Selected WordPress revision contains no "
                "restorable editable fields."
            )

        updated = self._update_post(
            post_id=change["post_id"],
            payload=payload,
            idempotency_key=request.idempotency_key,
        )

        return AdapterOperationResult(
            success=True,
            operation=AdapterOperation.ROLLBACK_CHANGE,
            site_id=request.site_id,
            normalized_url=request.normalized_url,
            message="WordPress change rolled back.",
            change_id=self._build_change_id(
                post=updated,
            ),
            metadata={
                "restored_revision_id": change[
                    "revision_id"
                ],
                "post_id": change["post_id"],
            },
        )

    def _resolve_post(
        self,
        *,
        normalized_url: str,
    ) -> dict[str, Any]:
        path = urlparse(
            normalized_url
        ).path.strip("/")

        if not path:
            raise WordPressAdapterError(
                "Cannot resolve a WordPress post from an empty URL path."
            )

        slug = path.rstrip("/").split("/")[-1]

        endpoint = (
            f"{self._config.content_type.value}"
            f"?slug={quote(slug, safe='')}"
        )

        response = self._request(
            method="GET",
            endpoint=endpoint,
        )

        payload = self._decode_json(
            response
        )

        if not isinstance(payload, list):
            raise WordPressAdapterError(
                "WordPress slug lookup must return an array."
            )

        if not payload:
            raise WordPressAdapterError(
                f"WordPress content not found for slug: {slug}",
                status_code=404,
            )

        exact_matches = [
            item
            for item in payload
            if isinstance(item, dict)
            and self._url_matches(
                normalized_url,
                item,
            )
        ]

        if len(exact_matches) == 1:
            return exact_matches[0]

        if len(payload) == 1:
            item = payload[0]

            if isinstance(item, dict):
                return item

        raise WordPressAdapterError(
            "WordPress slug lookup returned an ambiguous result."
        )

    def _url_matches(
        self,
        normalized_url: str,
        post: dict[str, Any],
    ) -> bool:
        link = post.get("link")

        if not isinstance(link, str):
            return False

        return self._normalize_url(link) == (
            self._normalize_url(normalized_url)
        )

    @staticmethod
    def _normalize_url(
        url: str,
    ) -> str:
        parsed = urlparse(url)

        return (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path.rstrip('/')}"
        ).lower()

    def _update_post(
        self,
        *,
        post_id: int,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError(
                "idempotency_key must not be empty"
            )

        endpoint = (
            f"{self._config.content_type.value}/"
            f"{post_id}"
        )

        response = self._request(
            method="POST",
            endpoint=endpoint,
            payload=payload,
            idempotency_key=idempotency_key,
        )

        result = self._decode_json(
            response
        )

        if not isinstance(result, dict):
            raise WordPressAdapterError(
                "WordPress update response must be an object."
            )

        return result

    def _get_revision(
        self,
        *,
        post_id: int,
        revision_id: int,
    ) -> dict[str, Any]:
        endpoint = (
            f"{self._config.content_type.value}/"
            f"{post_id}/revisions/{revision_id}"
        )

        response = self._request(
            method="GET",
            endpoint=endpoint,
        )

        payload = self._decode_json(
            response
        )

        if not isinstance(payload, dict):
            raise WordPressAdapterError(
                "WordPress revision response must be an object."
            )

        return payload

    def _resolve_latest_revision_id(
        self,
        *,
        post_id: int,
    ) -> int:
        endpoint = (
            f"{self._config.content_type.value}/"
            f"{post_id}/revisions"
        )

        response = self._request(
            method="GET",
            endpoint=endpoint,
        )

        payload = self._decode_json(
            response
        )

        if not isinstance(payload, list) or not payload:
            raise WordPressAdapterError(
                "WordPress did not return a usable revision."
            )

        latest = payload[0]

        if not isinstance(latest, dict):
            raise WordPressAdapterError(
                "Invalid WordPress revision payload."
            )

        revision_id = latest.get("id")

        if not isinstance(revision_id, int):
            raise WordPressAdapterError(
                "WordPress revision does not contain a valid id."
            )

        return revision_id

    def _request(
        self,
        *,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> WordPressHTTPResponse:
        url = urljoin(
            self._base_api_url,
            endpoint.lstrip("/"),
        )

        token = base64.b64encode(
            (
                f"{self._config.username}:"
                f"{self._config.application_password}"
            ).encode(
                "utf-8"
            )
        ).decode(
            "ascii"
        )

        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {token}",
            **self._config.extra_headers,
        }

        body: bytes | None = None

        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
            ).encode(
                "utf-8"
            )

            headers["Content-Type"] = (
                "application/json"
            )

        if idempotency_key is not None:
            headers["Idempotency-Key"] = (
                idempotency_key
            )

        try:
            response = self._transport(
                method.upper(),
                url,
                headers,
                body,
                self._config.timeout_seconds,
            )

        except WordPressAdapterError:
            raise

        if (
            response.status_code < 200
            or response.status_code >= 300
        ):
            raise WordPressAdapterError(
                (
                    "WordPress REST request returned "
                    f"unexpected status "
                    f"{response.status_code}"
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
        response: WordPressHTTPResponse,
    ) -> Any:
        if not response.body:
            raise WordPressAdapterError(
                "WordPress response body is empty."
            )

        try:
            return json.loads(
                response.body.decode(
                    "utf-8"
                )
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise WordPressAdapterError(
                "WordPress response is not valid JSON."
            ) from exc

    @staticmethod
    def _post_id(
        post: dict[str, Any],
    ) -> int:
        post_id = post.get("id")

        if not isinstance(post_id, int):
            raise WordPressAdapterError(
                "WordPress response does not contain a valid post id."
            )

        return post_id

    @staticmethod
    def _raw_value(
        field: Any,
    ) -> str:
        if isinstance(field, dict):
            value = field.get("raw")

            if value is None:
                value = field.get("rendered")

            if value is not None:
                return str(value)

        if field is None:
            return ""

        return str(field)

    @staticmethod
    def _read_meta_description(
        post: dict[str, Any],
    ) -> str:
        meta = post.get("meta")

        if not isinstance(meta, dict):
            return ""

        value = meta.get(
            "_seo_agent_meta_description"
        )

        if value is None:
            return ""

        return str(value)

    def _build_change_id(
        self,
        *,
        post: dict[str, Any],
    ) -> str:
        post_id = self._post_id(post)

        revision_id = self._resolve_latest_revision_id(
            post_id=post_id,
        )

        return (
            f"wordpress:"
            f"{post_id}:"
            f"{revision_id}"
        )

    @staticmethod
    def _parse_change_id(
        change_id: str,
    ) -> dict[str, int]:
        """
        Expected format:

            wordpress:{post_id}:{revision_id}

        Rollback requires an exact WordPress revision.
        """

        parts = change_id.split(":")

        if (
            len(parts) != 3
            or parts[0] != "wordpress"
        ):
            raise WordPressAdapterError(
                "Invalid WordPress rollback change id."
            )

        try:
            return {
                "post_id": int(parts[1]),
                "revision_id": int(parts[2]),
            }

        except ValueError as exc:
            raise WordPressAdapterError(
                "WordPress rollback change id contains invalid ids."
            ) from exc

    @staticmethod
    def _to_site_page(
        *,
        site_id: str,
        normalized_url: str,
        post: dict[str, Any],
    ) -> SitePage:
        return SitePage(
            site_id=site_id,
            normalized_url=normalized_url,
            title=WordPressSiteAdapter._raw_value(
                post.get("title")
            ),
            meta_description=(
                WordPressSiteAdapter._read_meta_description(
                    post
                )
            ),
            content=WordPressSiteAdapter._raw_value(
                post.get("content")
            ),
            status=str(
                post.get(
                    "status",
                    "unknown",
                )
            ),
            metadata={
                "wordpress_id": post.get("id"),
                "slug": post.get("slug"),
                "link": post.get("link"),
                "modified": post.get("modified"),
                "modified_gmt": post.get(
                    "modified_gmt"
                ),
                "content_type": (
                    "post"
                    if isinstance(
                        post.get("_links"),
                        dict,
                    )
                    else WordPressContentType.POSTS.value
                ),
            },
        )
