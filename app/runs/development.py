from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any

from app.models.gsc import RawGSCResponse


class StubGSCGateway:
    """Explicit development-only GSC source used before Stage 34 live wiring."""

    def fetch(
        self,
        *,
        site_id: str,
        property_url: str,
        credential: str,
        start_date: date,
        end_date: date,
    ) -> RawGSCResponse:
        material = json.dumps(
            {
                "site_id": site_id,
                "property_url": property_url,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "mode": "stub",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return RawGSCResponse(
            response_id="stub-gsc-" + hashlib.sha256(material.encode()).hexdigest(),
            site_id=site_id,
            fetch_date=end_date,
            raw_payload={"rows": []},
            created_at=datetime.now(timezone.utc),
        )


class EmptyBaselineProvider:
    """Explicit empty baseline used only for Stage 33 runtime wiring."""

    def load(
        self,
        *,
        site_id: str,
        normalized_url: str,
        normalized_query: str,
        end_date: date,
    ) -> tuple[list[Any], list[Any]]:
        return [], []
