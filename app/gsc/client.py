from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import time
from typing import Any, Callable
from urllib.parse import quote

from app.models.gsc import RawGSCResponse
from app.gsc.config import GSCClientConfig

class GSCClientError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code

@dataclass(frozen=True)
class GSCRequest:
    site_url: str
    start_date: date
    end_date: date
    dimensions: tuple[str, ...] = ("page", "query", "date")
    row_limit: int = 25000

    def validate(self) -> None:
        if not self.site_url.strip():
            raise ValueError("site_url must not be empty")
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        if not 1 <= self.row_limit <= 25000:
            raise ValueError("row_limit must be between 1 and 25000")

class GSCClient:
    SEARCH_ANALYTICS_PATH = "/webmasters/v3/sites/{site_url}/searchAnalytics/query"

    def __init__(
        self,
        config: GSCClientConfig,
        *,
        request_fn: Callable[..., Any],
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._request_fn = request_fn
        self._sleep = sleep_fn

    def query(
        self,
        *,
        site_id: str,
        site_url: str,
        start_date: date,
        end_date: date,
        dimensions: tuple[str, ...] = ("page", "query", "date"),
        row_limit: int | None = None,
    ) -> RawGSCResponse:
        request = GSCRequest(
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=dimensions,
            row_limit=row_limit if row_limit is not None else self.config.page_size,
        )
        request.validate()
        payload = self._query_with_pagination(request)
        now = datetime.now(timezone.utc)
        material = json.dumps(
            {
                "site_id": site_id,
                "site_url": site_url,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        response_id = "gsc-" + hashlib.sha256(material.encode()).hexdigest()
        return RawGSCResponse(
            response_id=response_id,
            site_id=site_id,
            fetch_date=now.date(),
            raw_payload=payload,
            created_at=now,
        )

    def _query_with_pagination(self, request: GSCRequest) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        start_row = 0
        page_count = 0
        fingerprints = set()
        incomplete_dates = []
        aggregation = None
        while True:
            if page_count >= self.config.max_pages:
                raise GSCClientError("GSC pagination limit reached; refusing partial response")
            page_count += 1
            page = self._request_page(request=request, start_row=start_row)
            page_rows = page.get("rows", [])
            if not isinstance(page_rows, list):
                raise GSCClientError("GSC response field 'rows' must be a list")
            if len(page_rows) > request.row_limit:
                raise GSCClientError("GSC page exceeds requested row limit")
            fingerprint = json.dumps(page_rows, sort_keys=True, separators=(",", ":"))
            if page_rows and fingerprint in fingerprints:
                raise GSCClientError("GSC pagination returned a repeated page")
            fingerprints.add(fingerprint)
            metadata = page.get("metadata", {})
            if not isinstance(metadata, dict):
                raise GSCClientError("Invalid GSC metadata")
            if metadata.get("first_incomplete_date"):
                incomplete_dates.append(date.fromisoformat(metadata["first_incomplete_date"]))
            page_aggregation = page.get("responseAggregationType")
            if page_aggregation is not None:
                if aggregation is not None and aggregation != page_aggregation:
                    raise GSCClientError("GSC aggregation changed between pages")
                aggregation = page_aggregation
            rows.extend(page_rows)
            if len(page_rows) < request.row_limit:
                break
            start_row += request.row_limit
        return {
            "startDate": request.start_date.isoformat(),
            "endDate": request.end_date.isoformat(),
            "rows": rows,
            "dimensions": list(request.dimensions),
            "dataState": self.config.data_state,
            "responseAggregationType": aggregation,
            "metadata": ({"first_incomplete_date": min(incomplete_dates).isoformat()} if incomplete_dates else {}),
        }

    def _request_page(self, *, request: GSCRequest, start_row: int) -> dict[str, Any]:
        body = {
            "startDate": request.start_date.isoformat(),
            "endDate": request.end_date.isoformat(),
            "dimensions": list(request.dimensions),
            "rowLimit": request.row_limit,
            "startRow": start_row,
            "dataState": self.config.data_state,
            "aggregationType": "auto",
        }
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.oauth_access_token:
            headers["Authorization"] = f"Bearer {self.config.oauth_access_token}"

        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._request_fn(
                    "POST",
                    self.SEARCH_ANALYTICS_PATH.format(site_url=quote(request.site_url, safe="")),
                    headers=headers,
                    json=body,
                    timeout=self.config.timeout_seconds,
                )
                status_code = getattr(response, "status_code", 200)
                if status_code in {429, 500, 502, 503, 504}:
                    raise GSCClientError(
                        f"GSC transient HTTP error: {status_code}",
                        retryable=True,
                        status_code=status_code,
                    )
                if status_code >= 400:
                    raise GSCClientError(
                        f"GSC HTTP error: {status_code}",
                        retryable=False,
                        status_code=status_code,
                    )
                payload = response.json()
                if not isinstance(payload, dict):
                    raise GSCClientError("GSC response JSON must be an object")
                return payload
            except GSCClientError as exc:
                if not exc.retryable or attempt >= self.config.max_retries:
                    raise
            except Exception as exc:
                if attempt >= self.config.max_retries:
                    raise GSCClientError(f"GSC transport error: {exc}") from exc
            self._sleep(min(2**attempt, 8))
        raise GSCClientError("GSC request failed")

