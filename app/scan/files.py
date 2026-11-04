"""The two files a search engine asks for first: robots.txt and the sitemap.

Pure text in, pure text out. What robots.txt already says is the customer's and
stays as it is, line for line; only what is missing is added. The one exception
is a rule keeping Google out of the whole site, the classic leftover from a
staging copy: dropping it is proposed by name, and the customer sees that before
putting the file up.

A sitemap that exists is never cut down to what one scan managed to read: a page
it lists stays unless the scan saw a plain reason for it to go.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin
from xml.sax.saxutils import escape

from app.models.site_files import SiteFileKind
from app.models.site_scan import ScanIssue, ScannedPage
from app.scan.crawler import CrawlResult, _identity, encode_url
from app.scan.issues import is_listable
from app.scan.robots_rules import blocking_lines

SITEMAP_LIMIT = 50_000


@dataclass(frozen=True)
class FilePlan:
    kind: SiteFileKind
    public_url: str
    text: str
    changes: tuple[str, ...]
    # Addresses the old sitemap had and this one leaves out.
    dropped: tuple[str, ...] = ()


def robots_can_be_judged(result: CrawlResult) -> bool:
    if result.robots_unreadable:
        return False
    # 200 with no text: a page at that address instead of a file, so there is none.
    return result.robots_text is not None or result.robots_status in {200, 404, 410}


def sitemap_can_be_judged(result: CrawlResult, issues: list[ScanIssue]) -> bool:
    """A scan that stopped early or met silence has no opinion on the sitemap.

    Nor does one that could not read robots.txt: it then did not know which
    pages Google is kept from, and a list could name them.
    """
    return not (
        result.sitemap_uncertain
        or result.pages_capped
        or not robots_can_be_judged(result)
        or any(issue.code == "page_no_answer" for issue in issues)
    )


def plan_site_files(
    *, base_url: str, result: CrawlResult, issues: list[ScanIssue], sitemap_declined: bool = False
) -> list[FilePlan]:
    """What to propose after a scan. Nothing, if the scan could not see the site."""
    if result.home_status != 200:
        return []
    plans: list[FilePlan] = []
    sitemap = None if sitemap_declined else _plan_sitemap(base_url, result, issues)
    if sitemap:
        plans.append(sitemap)
    # A robots.txt that answered with an error, or not at all, may be a perfectly
    # good file behind a bad moment: replacing it on that evidence could lose it.
    if robots_can_be_judged(result):
        declared = result.sitemap_urls[0] if result.sitemap_urls else None
        target = sitemap.public_url if sitemap else declared
        text, changes = improve_robots(result.robots_text, sitemap_url=target)
        if changes:
            plans.append(FilePlan(SiteFileKind.ROBOTS, urljoin(base_url, "/robots.txt"), text, tuple(changes)))
    return plans


def _plan_sitemap(base_url, result: CrawlResult, issues: list[ScanIssue]) -> FilePlan | None:
    # An address that would not say is not one that has no sitemap, and a page that
    # did not answer may be there in a minute: either way the list would be short.
    silent = (
        result.sitemap_uncertain
        or not robots_can_be_judged(result)
        or any(issue.code == "page_no_answer" for issue in issues)
    )
    if silent:
        return None
    if not result.sitemap_urls:
        listed = [page.url for page in result.pages if is_listable(page)]
        text = build_sitemap(listed)
        if text is None:
            return None
        # A site with more pages than a scan reads still gains from a first list.
        changes = ["created", *(["partial"] if result.pages_capped else [])]
        return FilePlan(SiteFileKind.SITEMAP, urljoin(base_url, "/sitemap.xml"), text, tuple(changes))
    # Not over an index or a sitemap with more in it than addresses: what the
    # scan found is in its issues, for the customer or their plugin to fix there.
    if result.pages_capped or result.sitemap_rich:
        return None
    kept, dropped, added = improved_list(result)
    if not dropped and not added:
        return None
    text = build_sitemap(kept)
    if text is None:
        return None
    return FilePlan(SiteFileKind.SITEMAP, result.sitemap_urls[0], text, ("improved",), tuple(dropped))


def _plainly_out(page: ScannedPage) -> bool:
    """A page with a reason the scan saw for it not to be in a sitemap."""
    if page.robots_blocked or page.noindex or page.redirected_to:
        return True
    if page.status_code in {404, 410}:
        return True
    return bool(page.canonical) and _identity(page.canonical) != _identity(page.url)


def improved_list(result: CrawlResult) -> tuple[list[str], list[str], list[str]]:
    """(the new list, what it drops, what it adds). Unread pages stay: no evidence against them."""
    read = {_identity(page.url): page for page in result.pages}
    listed = {_identity(url) for url in result.sitemap_pages}
    kept: list[str] = []
    dropped: list[str] = []
    for url in result.sitemap_pages:
        page = read.get(_identity(url))
        if page is not None and _plainly_out(page):
            dropped.append(url)
        else:
            kept.append(url)
    added = [page.url for page in result.pages if is_listable(page) and _identity(page.url) not in listed]
    return [*kept, *added], dropped, added


def build_sitemap(urls: list[str]) -> str | None:
    """A sitemap of these addresses; None when there are none."""
    listed = urls[:SITEMAP_LIMIT]
    if not listed:
        return None
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    # Sitemaps take only encoded addresses; a Persian one raw makes the file invalid.
    lines.extend(f"  <url><loc>{escape(encode_url(url))}</loc></url>" for url in listed)
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def improve_robots(current: str | None, *, sitemap_url: str | None) -> tuple[str, list[str]]:
    """The robots.txt to propose, and what it changes. No changes: nothing to propose."""
    if current is None or not current.strip():
        lines = ["User-agent: *", "Disallow:"]
        changes = ["created"]
    else:
        lines = current.splitlines()
        lines, removed = _without_block_all(lines)
        changes = ["removed_block_all"] if removed else []
    if sitemap_url and not any(_is_sitemap_line(line, sitemap_url) for line in lines):
        lines = [*_trimmed(lines), "", f"Sitemap: {sitemap_url}"]
        changes.append("added_sitemap")
    return "\n".join(lines).rstrip("\n") + "\n", changes


def _is_sitemap_line(line: str, sitemap_url: str) -> bool:
    if not line.lower().startswith("sitemap:"):
        return False
    return line.split(":", 1)[1].strip() == sitemap_url


def _trimmed(lines: list[str]) -> list[str]:
    trimmed = list(lines)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return trimmed


def _without_block_all(lines: list[str]) -> tuple[list[str], bool]:
    """Empty the "Disallow: /" lines that keep Google out, in the group Google obeys, nothing else."""
    blocking = set(blocking_lines(lines))
    # An empty rule, not no rule: a group with none is not valid everywhere.
    kept = ["Disallow:" if number in blocking else raw for number, raw in enumerate(lines)]
    return kept, bool(blocking)
