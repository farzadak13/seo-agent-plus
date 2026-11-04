"""What a scan found wrong. Deterministic: the same pages give the same list."""
from __future__ import annotations

from collections import defaultdict

from app.models.site_scan import IssueSeverity, ScanIssue, ScannedPage
from app.scan.crawler import CrawlResult, _identity
from app.scan.robots_rules import blocks_everything

# Persian letters are narrow, so a hard limit on characters would be wrong both
# ways; these are the plain cases, and the words shown to the customer say "about".
TITLE_TOO_SHORT = 15
TITLE_TOO_LONG = 70
DESCRIPTION_TOO_LONG = 170
THIN_WORDS = 100

_ORDER = {IssueSeverity.CRITICAL: 0, IssueSeverity.WARNING: 1, IssueSeverity.INFO: 2}


def is_listable(page: ScannedPage) -> bool:
    """A page that belongs in a sitemap: answers, may be read, and is its own canonical."""
    return (
        page.status_code == 200
        and page.redirected_to is None
        and not page.noindex
        and not page.robots_blocked
        and (page.canonical is None or _identity(page.canonical) == _identity(page.url))
    )


def robots_blocks_everything(text: str | None) -> bool:
    """A "Disallow: /" in the group Google obeys, as staging sites ship with."""
    return blocks_everything(text)


def build_issues(result: CrawlResult) -> list[ScanIssue]:
    issues: list[ScanIssue] = []
    issues.extend(_site_issues(result))
    for page in result.pages:
        issues.extend(_page_issues(page))
    issues.extend(_duplicates(result.pages, "title", "duplicate_title"))
    issues.extend(_duplicates(result.pages, "meta_description", "duplicate_meta_description"))
    return sorted(issues, key=lambda item: (_ORDER[item.severity], item.code, item.url or ""))


def _site_issues(result: CrawlResult) -> list[ScanIssue]:
    found: list[ScanIssue] = []
    if result.home_status != 200:
        found.append(
            ScanIssue(code="home_unreachable", severity=IssueSeverity.CRITICAL, detail={"status": result.home_status})
        )
    if robots_blocks_everything(result.robots_text):
        found.append(ScanIssue(code="robots_blocks_everything", severity=IssueSeverity.CRITICAL))
    if result.robots_unreadable:
        found.append(ScanIssue(code="robots_unreadable", severity=IssueSeverity.WARNING))
    elif result.robots_text is None:
        # Google treats a server error on robots.txt as "keep out for now".
        if result.robots_status is not None and result.robots_status >= 500:
            found.append(
                ScanIssue(code="robots_error", severity=IssueSeverity.CRITICAL, detail={"status": result.robots_status})
            )
        else:
            found.append(ScanIssue(code="no_robots", severity=IssueSeverity.WARNING))
    if result.sitemap_uncertain:
        found.append(ScanIssue(code="sitemap_unreadable", severity=IssueSeverity.WARNING))
    elif not result.sitemap_urls:
        found.append(ScanIssue(code="no_sitemap", severity=IssueSeverity.WARNING))
    elif not any(line.lower().startswith("sitemap:") for line in (result.robots_text or "").splitlines()):
        found.append(ScanIssue(code="sitemap_not_in_robots", severity=IssueSeverity.INFO))
    if result.sitemap_urls:
        missing = [page.url for page in result.pages if is_listable(page) and not page.in_sitemap]
        for url in missing:
            found.append(ScanIssue(code="not_in_sitemap", severity=IssueSeverity.INFO, url=url))
    return found


def _page_issues(page: ScannedPage) -> list[ScanIssue]:
    url = page.url
    found: list[ScanIssue] = []
    if page.robots_blocked:
        if page.in_sitemap:
            found.append(ScanIssue(code="sitemap_lists_blocked_page", severity=IssueSeverity.WARNING, url=url))
        return found
    # No answer, a server error, or a refusal by a firewall or a rate limit mid-scan:
    # none of them says anything about the page itself.
    if page.status_code is None or page.status_code >= 500 or page.status_code in {401, 403, 429}:
        # No answer says nothing about the page; it may be fine in a minute.
        found.append(ScanIssue(code="page_no_answer", severity=IssueSeverity.WARNING, url=url, detail={"status": page.status_code}))
        return found
    if page.status_code >= 400:
        code = "sitemap_lists_broken_page" if page.in_sitemap else "broken_page"
        found.append(ScanIssue(code=code, severity=IssueSeverity.WARNING, url=url, detail={"status": page.status_code}))
        return found
    if page.status_code != 200:
        return found
    if page.redirected_to and page.in_sitemap:
        found.append(
            ScanIssue(
                code="sitemap_lists_redirect",
                severity=IssueSeverity.INFO,
                url=url,
                detail={"to": page.redirected_to},
            )
        )
    if page.noindex:
        found.append(
            ScanIssue(
                code="noindex_in_sitemap" if page.in_sitemap else "noindex",
                severity=IssueSeverity.CRITICAL if page.in_sitemap else IssueSeverity.WARNING,
                url=url,
            )
        )
    if page.canonical and _identity(page.canonical) != _identity(page.redirected_to or url):
        found.append(
            ScanIssue(
                code="canonical_elsewhere", severity=IssueSeverity.WARNING, url=url, detail={"canonical": page.canonical}
            )
        )
    if not page.title:
        found.append(ScanIssue(code="missing_title", severity=IssueSeverity.CRITICAL, url=url))
    elif len(page.title) < TITLE_TOO_SHORT:
        found.append(
            ScanIssue(code="title_too_short", severity=IssueSeverity.WARNING, url=url, detail={"length": len(page.title)})
        )
    elif len(page.title) > TITLE_TOO_LONG:
        found.append(
            ScanIssue(code="title_too_long", severity=IssueSeverity.WARNING, url=url, detail={"length": len(page.title)})
        )
    if not page.meta_description:
        found.append(ScanIssue(code="missing_description", severity=IssueSeverity.WARNING, url=url))
    elif len(page.meta_description) > DESCRIPTION_TOO_LONG:
        found.append(
            ScanIssue(
                code="description_too_long",
                severity=IssueSeverity.INFO,
                url=url,
                detail={"length": len(page.meta_description)},
            )
        )
    if page.h1_count == 0:
        found.append(ScanIssue(code="missing_h1", severity=IssueSeverity.WARNING, url=url))
    elif page.h1_count > 1:
        found.append(ScanIssue(code="several_h1", severity=IssueSeverity.INFO, url=url, detail={"count": page.h1_count}))
    if page.word_count < THIN_WORDS:
        found.append(ScanIssue(code="thin_content", severity=IssueSeverity.INFO, url=url, detail={"words": page.word_count}))
    return found


def _duplicates(pages: list[ScannedPage], attribute: str, code: str) -> list[ScanIssue]:
    groups: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        value = getattr(page, attribute)
        if value and page.status_code == 200 and not page.noindex and page.redirected_to is None:
            groups[value].append(page.url)
    return [
        ScanIssue(code=code, severity=IssueSeverity.WARNING, url=url, detail={"others": len(urls) - 1})
        for urls in groups.values()
        if len(urls) > 1
        for url in urls
    ]
