"""What a scan found wrong. Deterministic: the same pages give the same list."""
from __future__ import annotations

import re
from collections import defaultdict, deque
from urllib.parse import unquote, urlsplit

from app.models.site_scan import IssueSeverity, ScanIssue, ScannedPage
from app.normalization.query import CHARACTER_TRANSLATION
from app.scan.crawler import CrawlResult, _identity
from app.scan.robots_rules import blocks_everything, closed_to

# Persian letters are narrow, so a hard limit on characters would be wrong both
# ways; these are the plain cases, and the words shown to the customer say "about".
TITLE_TOO_SHORT = 15
TITLE_TOO_LONG = 70
DESCRIPTION_TOO_LONG = 170
THIN_WORDS = 100
# Speed, from the page's own answer and markup (app.models.site_scan.PageSpeedSignals).
# Generous on purpose: this server is not where the customer's visitors are,
# so only what is slow by any measure is called slow.
# For the site's middle page, not any one page: one slow answer is a moment, not the site.
SLOW_MS = 2_500
HEAVY_HTML_KB = 500
BLOCKING_SCRIPTS = 3
UNSIZED_IMAGES = 3
NOT_LAZY_IMAGES = 5
OLD_FORMAT_IMAGES = 5

# Markup and structure (app.models.site_scan.PageMarkup, CrawlResult.links).
IMAGES_WITHOUT_ALT = 1
# Pages in another language of the Arabic script: their letters are right as they are.
_OTHER_SCRIPT_LANGS = ("ar", "ur", "ckb", "ku", "ps")
# More clicks than this from the home page and a page is far from where Google starts.
DEEP_CLICKS = 3
# Characters of a decoded path; a long Persian address is several times this on the wire.
LONG_PATH = 100
# A page that says "not found" and is short is a missing page answering 200.
SOFT_404_WORDS = 300
_NOT_FOUND = re.compile(
    r"(خطا|error|صفحه|page)\W{0,3}404|404\W{0,3}(خطا|error|صفحه|page|not found)|not found|یافت نشد|پیدا نشد|صفحه.{0,10}موجود نیست",
    re.IGNORECASE,
)
# The Arabic yeh and kaf look like Persian ones, but Google reads them as other
# letters: "كفش" and "کفش" are two different words to it.
_ARABIC_LETTERS = re.compile("[" + "".join(chr(code) for code in CHARACTER_TRANSLATION) + "]")
_PERSIAN_SCRIPT = re.compile(r"[\u0600-\u06ff]")
_LATIN_LETTERS = re.compile(r"[A-Za-z]")
# The robots of AI search and answers; closing them keeps the site out of what they say.
# Training-only ones (Google-Extended, CCBot) are left out: closing them changes
# nothing in what these tools show, and many sites close them on purpose.
AI_ROBOTS = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "PerplexityBot")

# Which version of the checks each code came with; a code not listed was there
# from the start (0). On the first scan after a version, what its checks find is
# not "new" since the last scan, only newly looked for (handler.new_since).
CHECKS_VERSION = 3
_PAGE_MARKUP = (
    "arabic_letters", "long_url", "soft_404", "missing_lang", "wrong_lang", "missing_rtl", "missing_viewport",
    "missing_open_graph", "images_without_alt", "mixed_content", "schema_invalid", "schema_persian_digits",
    "schema_bad_date", "no_structured_data", "duplicate_content", "internal_link_redirects", "orphan_page",
    "deep_page", "ai_robots_blocked", "no_llms_txt",
)
# From the page fetcher: every scan since makes these.
_REDIRECTS = ("redirect_chain", "temporary_redirect")
# From the server probe: only a scan whose probe got an answer (handler).
_SERVER = (
    "ssl_invalid", "ssl_expiring", "http_not_redirected", "http_redirect_temporary", "no_hsts", "no_https",
    "site_address_http", "www_not_redirected", "no_compression",
)
ADDED_IN = {**{code: 1 for code in _PAGE_MARKUP}, **{code: 2 for code in _REDIRECTS}, **{code: 3 for code in _SERVER}}
# Days a certificate has left before the site is told to renew it.
CERTIFICATE_DAYS = 21

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
    for page in result.pages:
        issues.extend(speed_issues(page))
    issues.extend(_slow_site(result.pages))
    issues.extend(_duplicates(result.pages, "title", "duplicate_title"))
    issues.extend(_duplicates(result.pages, "meta_description", "duplicate_meta_description"))
    for page in result.pages:
        issues.extend(markup_issues(page))
    # A page copied whole already shows as a duplicate title or description:
    # one copy is one problem, not three.
    already = {issue.url for issue in issues if issue.code in {"duplicate_title", "duplicate_meta_description"}}
    issues.extend(issue for issue in _duplicate_text(result.pages) if issue.url not in already)
    issues.extend(_link_issues(result))
    issues.extend(_ai_issues(result))
    issues.extend(_server_issues(result))
    for page in result.pages:
        issues.extend(_redirect_issues(page))
    issues = _with_linking_page(issues, result)
    return sorted(issues, key=lambda item: (_ORDER[item.severity], item.code, item.url or ""))


def _persian(page: ScannedPage) -> bool:
    text = f"{page.title or ''} {page.h1 or ''}"
    return len(_PERSIAN_SCRIPT.findall(text)) > len(_LATIN_LETTERS.findall(text))


def markup_issues(page: ScannedPage) -> list[ScanIssue]:
    """Language, phones, sharing, images, structured data and the Persian-only ones, for one page."""
    if page.status_code != 200 or page.redirected_to is not None or page.noindex:
        return []
    url = page.url
    found: list[ScanIssue] = []
    markup = page.markup
    other_script = bool(markup and markup.lang and markup.lang.startswith(_OTHER_SCRIPT_LANGS))
    for field in ("title", "h1", "meta_description"):
        if not other_script and _ARABIC_LETTERS.search(getattr(page, field) or ""):
            found.append(ScanIssue(code="arabic_letters", severity=IssueSeverity.WARNING, url=url, detail={"in": field}))
            break
    if len(unquote(urlsplit(url).path)) > LONG_PATH:
        found.append(ScanIssue(code="long_url", severity=IssueSeverity.INFO, url=url))
    if _NOT_FOUND.search(f"{page.title or ''} {page.h1 or ''}") and page.word_count < SOFT_404_WORDS:
        found.append(ScanIssue(code="soft_404", severity=IssueSeverity.WARNING, url=url))
    if markup is None:
        return found
    persian = _persian(page) and not other_script
    if not markup.lang:
        found.append(ScanIssue(code="missing_lang", severity=IssueSeverity.INFO, url=url))
    elif persian and not markup.lang.startswith("fa"):
        found.append(ScanIssue(code="wrong_lang", severity=IssueSeverity.INFO, url=url, detail={"lang": markup.lang}))
    if persian and not markup.rtl:
        found.append(ScanIssue(code="missing_rtl", severity=IssueSeverity.INFO, url=url))
    if not markup.viewport:
        found.append(ScanIssue(code="missing_viewport", severity=IssueSeverity.WARNING, url=url))
    if not markup.open_graph:
        found.append(ScanIssue(code="missing_open_graph", severity=IssueSeverity.INFO, url=url))
    if markup.images_without_alt >= IMAGES_WITHOUT_ALT:
        found.append(ScanIssue(
            code="images_without_alt", severity=IssueSeverity.INFO, url=url, detail={"count": markup.images_without_alt},
        ))
    if markup.insecure_resources:
        found.append(ScanIssue(
            code="mixed_content", severity=IssueSeverity.WARNING, url=url, detail={"count": markup.insecure_resources},
        ))
    for problem, code in (("invalid", "schema_invalid"), ("persian_digits", "schema_persian_digits"), ("bad_date", "schema_bad_date")):
        if problem in markup.schema_problems:
            found.append(ScanIssue(code=code, severity=IssueSeverity.WARNING, url=url))
    return found


def _duplicate_text(pages: list[ScannedPage]) -> list[ScanIssue]:
    """Pages saying exactly the same, each its own canonical: Google shows one and drops the rest."""
    groups: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        if page.markup and page.markup.text_hash and is_listable(page):
            groups[page.markup.text_hash].append(page.url)
    return [
        ScanIssue(code="duplicate_content", severity=IssueSeverity.WARNING, url=url, detail={"others": len(urls) - 1})
        for urls in groups.values()
        if len(urls) > 1
        for url in urls
    ]


def _link_issues(result: CrawlResult) -> list[ScanIssue]:
    """From which page links to which: links to moved pages, pages nothing links to, deep pages."""
    by_identity = {_identity(page.url): page for page in result.pages}
    linked_from: dict[str, set[str]] = defaultdict(set)
    for source, targets in result.links.items():
        for target in targets:
            if target != source:
                linked_from[target].add(source)
                moved = by_identity.get(target)
                if moved is not None and moved.redirected_to:
                    linked_from[_identity(moved.redirected_to)].add(source)
    found: list[ScanIssue] = []
    for identity, page in by_identity.items():
        if page.status_code == 200 and page.redirected_to and linked_from.get(identity):
            found.append(ScanIssue(
                code="internal_link_redirects", severity=IssueSeverity.INFO, url=page.url,
                detail={"to": page.redirected_to, "pages": len(linked_from[identity])},
            ))
    # Both need the whole site read: a page past the cap may be the one that links.
    if result.pages_capped or not result.pages:
        return found
    home = _identity(result.pages[0].url)
    depth = {home: 0}
    queue = deque([home])
    while queue:
        current = queue.popleft()
        for target in result.links.get(current, ()):
            moved = by_identity.get(target)
            for reached in (target, _identity(moved.redirected_to) if moved and moved.redirected_to else None):
                if reached and reached not in depth and reached in by_identity:
                    depth[reached] = depth[current] + 1
                    queue.append(reached)
    for identity, page in by_identity.items():
        if identity == home or not is_listable(page):
            continue
        if not linked_from.get(identity):
            if page.in_sitemap:
                found.append(ScanIssue(code="orphan_page", severity=IssueSeverity.INFO, url=page.url))
        elif depth.get(identity, 0) > DEEP_CLICKS:
            found.append(ScanIssue(code="deep_page", severity=IssueSeverity.INFO, url=page.url, detail={"clicks": depth[identity]}))
    return found


def _ai_issues(result: CrawlResult) -> list[ScanIssue]:
    found: list[ScanIssue] = []
    if result.home_status != 200:
        return found
    lines = (result.robots_text or "").splitlines()
    closed = [robot for robot in AI_ROBOTS if closed_to(lines, robot)]
    if closed and not robots_blocks_everything(result.robots_text):
        found.append(ScanIssue(code="ai_robots_blocked", severity=IssueSeverity.INFO, detail={"robots": closed}))
    if not result.llms_txt:
        found.append(ScanIssue(code="no_llms_txt", severity=IssueSeverity.INFO))
    if not any(page.markup and page.markup.schema_types for page in result.pages):
        found.append(ScanIssue(code="no_structured_data", severity=IssueSeverity.INFO))
    return found


def _redirect_issues(page: ScannedPage) -> list[ScanIssue]:
    """A page reached through several redirects, or through a temporary one."""
    if not page.redirected_to or page.status_code != 200:
        return []
    found: list[ScanIssue] = []
    if page.redirect_hops >= 2:
        found.append(ScanIssue(
            code="redirect_chain", severity=IssueSeverity.WARNING, url=page.url,
            detail={"hops": page.redirect_hops, "to": page.redirected_to},
        ))
    if page.redirect_code in {302, 303, 307}:
        found.append(ScanIssue(
            code="temporary_redirect", severity=IssueSeverity.INFO, url=page.url,
            detail={"status": page.redirect_code, "to": page.redirected_to},
        ))
    return found


def _server_issues(result: CrawlResult) -> list[ScanIssue]:
    """One site, one address, over a certificate that holds: from the server, once a scan."""
    probe = result.probe
    if probe is None or not probe.known or not result.pages:
        return []
    if result.home_status != 200:
        # The certificate may be why the home page could not be read: say so.
        return [ScanIssue(code="ssl_invalid", severity=IssueSeverity.CRITICAL)] if probe.certificate_invalid else []
    found: list[ScanIssue] = []
    secure = urlsplit(result.pages[0].redirected_to or result.pages[0].url).scheme == "https"
    if secure:
        if probe.certificate_invalid:
            found.append(ScanIssue(code="ssl_invalid", severity=IssueSeverity.CRITICAL))
        elif probe.certificate_days is not None and probe.certificate_days <= CERTIFICATE_DAYS:
            found.append(ScanIssue(code="ssl_expiring", severity=IssueSeverity.WARNING, detail={"days": max(0, probe.certificate_days)}))
        if probe.http_to_https is False:
            found.append(ScanIssue(code="http_not_redirected", severity=IssueSeverity.WARNING))
        elif probe.http_to_https and probe.http_redirect_code in {302, 303, 307}:
            found.append(ScanIssue(code="http_redirect_temporary", severity=IssueSeverity.INFO, detail={"status": probe.http_redirect_code}))
        if probe.hsts is False:
            found.append(ScanIssue(code="no_hsts", severity=IssueSeverity.INFO))
    elif probe.https_answers is True:
        found.append(ScanIssue(code="site_address_http", severity=IssueSeverity.WARNING))
    elif probe.https_answers is False:
        found.append(ScanIssue(code="no_https", severity=IssueSeverity.WARNING))
    if probe.other_host_redirects is False:
        found.append(ScanIssue(code="www_not_redirected", severity=IssueSeverity.WARNING, detail={"host": probe.other_host}))
    if probe.compressed is False:
        found.append(ScanIssue(code="no_compression", severity=IssueSeverity.WARNING))
    return found


def _with_linking_page(issues: list[ScanIssue], result: CrawlResult) -> list[ScanIssue]:
    """A broken or moved page says which page links to it, so the link can be found and fixed."""
    sources: dict[str, str] = {}
    by_identity = {_identity(page.url): page.url for page in result.pages}
    for source, targets in result.links.items():
        for target in targets:
            if target != source and target not in sources and source in by_identity:
                sources[target] = by_identity[source]
    return [
        issue.model_copy(update={"detail": {**issue.detail, "from": sources[_identity(issue.url)]}})
        if issue.code in {"broken_page", "sitemap_lists_broken_page", "internal_link_redirects", "redirect_chain", "temporary_redirect"}
        and issue.url and _identity(issue.url) in sources
        else issue
        for issue in issues
    ]


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


def speed_issues(page: ScannedPage) -> list[ScanIssue]:
    """What the page's answer and markup say is slowing it; each with its own number."""
    speed = page.speed
    if speed is None or page.status_code != 200:
        return []
    url = page.url
    # Information, not warnings: they slow a page, they do not keep it from Google,
    # and the health score is about what is in Google's way.
    checks = [
        ("heavy_html", IssueSeverity.INFO, speed.html_kb >= HEAVY_HTML_KB, {"kb": speed.html_kb}),
        ("render_blocking_scripts", IssueSeverity.INFO, speed.blocking_scripts >= BLOCKING_SCRIPTS, {"count": speed.blocking_scripts}),
        ("images_without_size", IssueSeverity.INFO, speed.images_unsized >= UNSIZED_IMAGES, {"count": speed.images_unsized}),
        ("images_not_lazy", IssueSeverity.INFO, speed.images_not_lazy >= NOT_LAZY_IMAGES, {"count": speed.images_not_lazy}),
        ("old_image_formats", IssueSeverity.INFO, speed.images_old_format >= OLD_FORMAT_IMAGES, {"count": speed.images_old_format}),
    ]
    return [ScanIssue(code=code, severity=severity, url=url, detail=detail) for code, severity, hit, detail in checks if hit]


def _slow_site(pages: list[ScannedPage]) -> list[ScanIssue]:
    times = sorted(page.speed.fetch_ms for page in pages if page.speed is not None and page.status_code == 200)
    if not times:
        return []
    middle = times[len(times) // 2]
    if middle < SLOW_MS:
        return []
    return [ScanIssue(code="slow_response", severity=IssueSeverity.WARNING, url=None, detail={"ms": middle})]


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
