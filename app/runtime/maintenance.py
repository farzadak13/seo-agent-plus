"""Clearing out sign-in records nobody can use any more.

Every sign-in writes records: a session, a new version of it each time it is
touched or revoked, and a single-use state each time a Google flow starts.
None of them is history worth keeping once it can no longer be used, and
without this they would only accumulate.

Removed, once a day past the point of use:
- Google sign-in and Search Console connection states (they last ten minutes);
- sessions that have expired, been revoked, or sat idle past the timeout;
- and, for live sessions, every version but the latest, which is the only
  one ever read.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.accounts.google_login import STATE_AGGREGATE as LOGIN_STATE
from app.accounts.service import IDLE_TIMEOUT
from app.accounts.store import SESSION
from app.onboarding.google_oauth import STATE_AGGREGATE as OAUTH_STATE

log = logging.getLogger(__name__)

GRACE = timedelta(days=1)
PURGE_INTERVAL_SECONDS = 60 * 60

_STATES = """
DELETE FROM persistence_records r
 USING (SELECT aggregate_type, aggregate_id FROM persistence_records
         WHERE aggregate_type = ANY(%(states)s) AND version = 1 AND created_at < %(cutoff)s) old
 WHERE r.aggregate_type = old.aggregate_type AND r.aggregate_id = old.aggregate_id
"""

# A session aggregate is judged by its latest version, the only one read.
_DEAD_SESSIONS = """
DELETE FROM persistence_records r
 USING (SELECT DISTINCT ON (aggregate_id) aggregate_id, payload FROM persistence_records
         WHERE aggregate_type = %(session)s
         ORDER BY aggregate_id, version DESC) latest
 WHERE r.aggregate_type = %(session)s AND r.aggregate_id = latest.aggregate_id
   AND ((latest.payload->>'expires_at')::timestamptz < %(cutoff)s
     OR (latest.payload->>'revoked_at')::timestamptz < %(cutoff)s
     OR (latest.payload->>'last_seen_at')::timestamptz < %(idle_cutoff)s)
"""

_SUPERSEDED_SESSION_VERSIONS = """
DELETE FROM persistence_records r
 USING (SELECT aggregate_id, MAX(version) AS latest FROM persistence_records
         WHERE aggregate_type = %(session)s GROUP BY aggregate_id) m
 WHERE r.aggregate_type = %(session)s AND r.aggregate_id = m.aggregate_id
   AND r.version < m.latest
"""


class RecordPurge:
    def __init__(
        self,
        dsn: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        connection_factory: Callable | None = None,
    ) -> None:
        import psycopg

        self._dsn = dsn
        self._clock = clock
        self._connect = connection_factory or psycopg.connect

    def run_round(self) -> dict[str, int]:
        cutoff = self._clock() - GRACE
        params = {
            "states": [LOGIN_STATE, OAUTH_STATE],
            "session": SESSION,
            "cutoff": cutoff,
            "idle_cutoff": cutoff - IDLE_TIMEOUT,
        }
        removed = {}
        with self._connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                for name, sql in (
                    ("states", _STATES),
                    ("dead_sessions", _DEAD_SESSIONS),
                    ("superseded_session_versions", _SUPERSEDED_SESSION_VERSIONS),
                ):
                    cursor.execute(sql, params)
                    removed[name] = cursor.rowcount
        if any(removed.values()):
            log.info("record purge: %s", removed)
        return removed
