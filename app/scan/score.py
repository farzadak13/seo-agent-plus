"""One number for what the scan found: 100 is nothing in Google's way.

Counted by kind of problem, not by page: a shop with 400 product pages missing
a description has one problem to fix, in one template, not 400. Within a kind,
how much of the site it touches still counts, so one stray page weighs less
than every page.

A sum of weights cannot say "Google cannot see this site", so that is said by
a ceiling: a site closed to Google, or a problem that keeps most pages out,
cannot score as nearly healthy. A site that did not answer gets no score.
"""
from __future__ import annotations

from app.models.site_scan import IssueSeverity, ScanIssue, ScannedPage

WEIGHTS = {IssueSeverity.CRITICAL: 15, IssueSeverity.WARNING: 5, IssueSeverity.INFO: 1}
_RANK = {IssueSeverity.INFO: 0, IssueSeverity.WARNING: 1, IssueSeverity.CRITICAL: 2}
# A kind of problem on a single page still costs this share of its weight.
FLOOR = 0.4
# Nothing could be judged.
NO_SCORE = frozenset({"home_unreachable"})
# The whole site is out of Google.
CLOSED_CEILING = 10
# A server error on robots.txt keeps Google out too, for as long as it lasts.
CLOSED = frozenset({"robots_blocks_everything", "robots_error"})
# A problem that keeps at least half the pages out of Google.
WIDESPREAD_CEILING = 40
WIDESPREAD_SHARE = 0.5
# A page that did not answer this time is not the site's fault (yet).
NOT_COUNTED = frozenset({"page_no_answer"})


def health_score(issues: list[ScanIssue], pages: list[ScannedPage]) -> int | None:
    codes = {issue.code for issue in issues}
    if codes & NO_SCORE:
        return None
    read = max(1, sum(1 for page in pages if page.status_code is not None))
    kinds: dict[str, tuple[IssueSeverity, set[str], bool]] = {}
    for issue in issues:
        if issue.code in NOT_COUNTED:
            continue
        severity, urls, site_wide = kinds.get(issue.code, (issue.severity, set(), False))
        if _RANK.get(issue.severity, 0) > _RANK.get(severity, 0):
            severity = issue.severity
        if issue.url:
            urls.add(issue.url)
        kinds[issue.code] = (severity, urls, site_wide or not issue.url)
    penalty = 0.0
    ceiling = 100
    for severity, urls, site_wide in kinds.values():
        share = 1.0 if site_wide else min(1.0, len(urls) / read)
        penalty += WEIGHTS.get(severity, 1) * max(FLOOR, share)
        if severity == IssueSeverity.CRITICAL and not site_wide and share >= WIDESPREAD_SHARE:
            ceiling = min(ceiling, WIDESPREAD_CEILING)
    if codes & CLOSED:
        ceiling = min(ceiling, CLOSED_CEILING)
    return max(0, min(ceiling, round(100 - penalty)))
