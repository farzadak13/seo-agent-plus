"""Keyset pagination cursors.

A cursor carries the sort key of the last row on a page — never an offset —
so inserts during paging cannot cause a row to be skipped or repeated.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime

from app.persistence.contracts import InvalidCursorError


def encode_cursor(*, created_at: datetime, aggregate_id: str) -> str:
    raw = json.dumps(
        [created_at.isoformat(), aggregate_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at_text, aggregate_id = json.loads(raw)
        created_at = datetime.fromisoformat(created_at_text)
    except Exception as exc:
        raise InvalidCursorError("Pagination cursor is not valid.") from exc
    if not isinstance(aggregate_id, str) or not aggregate_id:
        raise InvalidCursorError("Pagination cursor is not valid.")
    return created_at, aggregate_id


__all__ = ["decode_cursor", "encode_cursor"]
