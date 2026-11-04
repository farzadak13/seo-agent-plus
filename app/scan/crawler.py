"""Read a site the way a search engine does: robots.txt, the sitemap, then its pages.

Everything here comes from someone else's server and is treated that way: every
address goes through the guard that refuses this server's own network
(app.net.guard), every answer is read up to a limit, and an XML document that
declares anything of its own is not parsed at all.

A site with no sitemap is read by following the links from its home page, which
is also how the pages for a new sitemap are found.
"""
from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ElementTree
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import Request
from urllib.robotparser import RobotFileParser

from app.models.site_scan import ScannedPage
from app.net.guard import UnsafeAddressError, open_public, same_site
from app.normalization.url import canonicalize_url
from app.scan.robots_rules import google_allows

USER_AGENT = "HoshyarSEO-Scan/1.0 (+https://hoshyarseo.ir/)"
ROBOTS_NAME = "HoshyarSEO-Scan"
MAX_QUEUE = 2_000
# What a sitemap can carry beside its addresses: other languages, images, videos.
RICH_SITEMAP_MARKERS = (b"xhtml:link", b"image:image", b"video:video", b"news:news")

MAX_BODY_BYTES = 3_000_000
# Python's HTML parser slows down badly on hostile markup (8 s for 3 MB on the
# server's 3.12), outside any deadline; titles and most links come early anyway.
MAX_PARSE_BYTES = 1_000_000
MAX_SITEMAP_BYTES = 20_000_000
MAX_SITEMAP_URLS = 5_000
MAX_CHILD_SITEMAPS = 20
MAX_ROBOTS_BYTES = 500_000
COMMON_SITEMAPS = ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml", "/wp-sitemap.xml")
MAX_CRAWL_DELAY_SECONDS = 5.0
MAX_FIELD_CHARACTERS = 1000
# Not pages: fetching them only wastes the customer's bandwidth and ours.
SKIPPED_EXTENSIONS = frozenset(
    ".jpg .jpeg .png .gif .webp .svg .ico .bmp .avif .pdf .zip .rar .gz .tar .7z .mp3 .mp4 .webm .avi "
    ".mov .css .js .json .xml .txt .doc .docx .xls .xlsx .ppt .pptx .woff .woff2 .ttf .eot".split()
)


@dataclass(frozen=True)
class Fetched:
    # None: no answer, or the address was refused.
    status: int | None
    body: bytes = b""
    # False: cut short by a limit, a deadline or a dropped connection.
    complete: bool = True
    final_url: str | None = None
    content_type: str = ""
    x_robots: str = ""


Fetcher = Callable[[str, int], Fetched]

# A thread left behind by a server that holds a request open keeps its slot until
# the socket ends; this is the most of them there can be.
_FETCH_SLOTS = threading.BoundedSemaphore(16)


def encode_url(url: str) -> str:
    """The address as it goes on the wire: Persian in a link is normal, and raw it is no request."""
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    try:
        host.encode("ascii")
        netloc = parts.netloc
    except UnicodeEncodeError:
        port = f":{parts.port}" if parts.port else ""
        netloc = host.encode("idna").decode("ascii") + port
    return urlunsplit((
        parts.scheme,
        netloc,
        quote(parts.path, safe="/%:@!$&'()*+,;=-._~"),
        quote(parts.query, safe="/%:@!$&'()*+,;=?-._~"),
        "",
    ))


def _read_bounded(response, limit: int, deadline: float) -> tuple[bytes, bool]:
    """Up to ``limit`` bytes, for no longer than the deadline, and whether that was all of it.

    A socket timeout is per read, so a server sending a byte every few seconds
    never trips it; read1 returns what has arrived, so the clock gets a say.
    """
    chunks: list[bytes] = []
    size = 0
    ended = False
    while size <= limit and time.monotonic() < deadline:
        chunk = response.read1(65536)
        if not chunk:
            ended = True
            break
        chunks.append(chunk)
        size += len(chunk)
    # read1 reports a connection dropped mid-body as a plain end.
    declared = response.headers.get("Content-Length", "")
    whole = ended and not (declared.isdigit() and int(declared) != size)
    return b"".join(chunks), whole


def _fetch_once(url: str, limit: int, *, timeout: float, deadline: float) -> Fetched:
    try:
        request = Request(
            encode_url(url),
            method="GET",
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml,text/plain,*/*"},
        )
        # same_host: a redirect off the site is the end of what is ours to read.
        with open_public(request, timeout=timeout, same_host=True) as response:
            body, whole = _read_bounded(response, limit, deadline)
            return Fetched(
                status=response.status,
                body=body,
                complete=whole,
                final_url=response.geturl(),
                content_type=response.headers.get("Content-Type", ""),
                x_robots=response.headers.get("X-Robots-Tag", ""),
            )
    except HTTPError as exc:
        with exc:
            return Fetched(status=exc.code, final_url=url)
    except (URLError, UnsafeAddressError, TimeoutError, OSError, ValueError, HTTPException):
        return Fetched(status=None)


def default_fetcher(url: str, limit: int, *, timeout: float = 10.0, total_seconds: float = 30.0) -> Fetched:
    """One address, in bounded time whatever the other side does.

    The worker runs one job at a time, so a site that holds a request open holds
    up every customer's changes. Headers can be dripped too, where no read loop
    can reach, so the whole request runs in a thread that is left behind if it
    overstays; it ends when its socket does.
    """
    if not _FETCH_SLOTS.acquire(blocking=False):
        return Fetched(status=None)  # too many left behind already: no more of them
    answer: list[Fetched] = []
    deadline = time.monotonic() + total_seconds

    def work() -> None:
        try:
            answer.append(_fetch_once(url, limit, timeout=timeout, deadline=deadline))
        finally:
            _FETCH_SLOTS.release()

    thread = threading.Thread(target=work, daemon=True, name="scan-fetch")
    thread.start()
    thread.join(total_seconds + timeout)
    return answer[0] if answer else Fetched(status=None)


class _PageParser(HTMLParser):
    """The parts of a page that say how a search engine should take it."""

    _SKIPPED_TEXT = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.meta_description: str | None = None
        self.h1: str | None = None
        self.h1_count = 0
        self.canonical: str | None = None
        self.noindex = False
        self.links: list[str] = []
        self.words = 0
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        values = {name: value or "" for name, value in attrs}
        if tag in self._SKIPPED_TEXT:
            self._skip_depth += 1
            return
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag == "meta":
            name = values.get("name", "").lower()
            if name == "description" and self.meta_description is None:
                self.meta_description = " ".join(values.get("content", "").split()) or None
            elif name in {"robots", "googlebot"} and _says_noindex(values.get("content", "")):
                self.noindex = True
        elif tag == "link" and "canonical" in values.get("rel", "").lower().split() and self.canonical is None:
            self.canonical = values.get("href", "").strip() or None
        elif tag == "title" and self.title is None and self._capture is None:
            self._capture, self._buffer = "title", []
        elif tag == "h1":
            self.h1_count += 1
            if self.h1 is None and self._capture is None:
                self._capture, self._buffer = "h1", []

    def handle_endtag(self, tag):
        if tag in self._SKIPPED_TEXT and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == self._capture:
            text = " ".join("".join(self._buffer).split()) or None
            if tag == "title":
                self.title = text
            else:
                self.h1 = text
            self._capture = None

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._capture:
            self._buffer.append(data)
        if self._capture != "title":
            self.words += len(data.split())


def _says_noindex(value: str) -> bool:
    directives = {part.strip().lower() for part in value.replace(";", ",").split(",")}
    return "noindex" in directives or "none" in directives


def _decompress(body: bytes, limit: int) -> bytes | None:
    if body[:2] != b"\x1f\x8b":
        return body
    try:
        # Bounded by hand: a small file can inflate to any size.
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = inflater.decompress(body, limit + 1)
    except zlib.error:
        return None
    return None if len(data) > limit else data


def parse_sitemap(body: bytes) -> tuple[list[str], list[str]]:
    """(pages, child sitemaps). Nothing, for anything that is not a plain sitemap."""
    data = _decompress(body, MAX_SITEMAP_BYTES)
    if not data:
        return [], []
    head = data[:2000].lower()
    # Entity tricks need a declaration; a real sitemap has no use for one.
    if b"<!doctype" in data[:4096].lower() or b"<!entity" in data.lower():
        return [], []
    if b"<urlset" not in head and b"<sitemapindex" not in head:
        return [], []
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return [], []
    tag = root.tag.rsplit("}", 1)[-1]
    # Only an entry's own <loc>: image and video extensions carry <loc> of their
    # own, and those are files, not pages.
    locations = [
        (loc.text or "").strip()
        for entry in root
        if entry.tag.rsplit("}", 1)[-1] in {"url", "sitemap"}
        for loc in entry
        if loc.tag.rsplit("}", 1)[-1] == "loc" and (loc.text or "").strip()
    ]
    if tag == "sitemapindex":
        return [], locations
    if tag == "urlset":
        return locations, []
    return [], []


def _clean_link(base: str, href: str) -> str | None:
    """An absolute page address, without its fragment; None for anything else."""
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    try:
        parts = urlsplit(urljoin(base, href))
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    path = parts.path or "/"
    extension = path.rsplit("/", 1)[-1].rpartition(".")[2].lower()
    if "." in path.rsplit("/", 1)[-1] and f".{extension}" in SKIPPED_EXTENSIONS:
        return None
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _without_query(url: str) -> str:
    return _identity(url).split("?", 1)[0].rstrip("/")


def _identity(url: str) -> str:
    try:
        return canonicalize_url(url).rstrip("/")
    except ValueError:
        return url


@dataclass
class CrawlResult:
    home_status: int | None = None
    robots_text: str | None = None
    robots_status: int | None = None
    # A robots.txt exists but its text could not be trusted to be all of it.
    robots_unreadable: bool = False
    # An address where a sitemap would be did not say whether one is there.
    sitemap_uncertain: bool = False
    sitemap_urls: list[str] = field(default_factory=list)
    # Every page the sitemap lists, read by the scan or not.
    sitemap_pages: list[str] = field(default_factory=list)
    sitemap_page_count: int = 0
    # The site's sitemap is an index, or carries languages, images or videos: a
    # plain list in its place would lose them.
    sitemap_rich: bool = False
    pages: list[ScannedPage] = field(default_factory=list)
    pages_capped: bool = False


class SiteCrawler:
    def __init__(
        self,
        *,
        fetcher: Fetcher = default_fetcher,
        max_pages: int = 100,
        # The worker runs one job at a time: everyone else waits for this one.
        time_budget_seconds: float = 120.0,
        delay_seconds: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetch = fetcher
        self._max_pages = max_pages
        self._budget = time_budget_seconds
        self._delay = delay_seconds
        self._clock = clock
        self._sleep = sleep
        self._robots_lines: list[str] = []
        self._sitemap_rich = False

    def crawl(self, base_url: str) -> CrawlResult:
        result = CrawlResult()
        deadline = self._clock() + self._budget
        robots = self._read_robots(base_url, result)
        asked = robots.crawl_delay("*") or 0
        delay = max(self._delay, min(float(asked), MAX_CRAWL_DELAY_SECONDS))
        self._sitemap_rich = False
        sitemap_pages = self._read_sitemaps(base_url, result, deadline)
        result.sitemap_rich = self._sitemap_rich
        result.sitemap_page_count = len(sitemap_pages)
        result.sitemap_pages = list(sitemap_pages)
        listed = {_identity(url) for url in sitemap_pages}

        queue: list[str] = []
        queued: set[str] = set()
        paths: set[str] = set()
        for url in [base_url, *sitemap_pages]:
            if _identity(url) not in queued:
                queued.add(_identity(url))
                paths.add(_without_query(url))
                queue.append(url)
        position = 0
        while position < len(queue):
            if len(result.pages) >= self._max_pages or self._clock() >= deadline:
                result.pages_capped = True
                break
            url = queue[position]
            if position:
                self._sleep(delay)
            position += 1
            # The home page is read whatever robots.txt says, as anyone opening the
            # site would: "you are keeping Google out" must not look like "site down".
            read = self._read_page(url, robots=robots, in_sitemap=_identity(url) in listed, always=position == 1)
            if read is None:
                continue
            page, links = read
            if position == 1:
                result.home_status = page.status_code
            result.pages.append(page)
            for href in links:
                link = _clean_link(page.redirected_to or url, href)
                if link is None or not _is_on_site(base_url, link):
                    continue
                identity = _identity(link)
                # The same page with ?replytocom=, ?orderby= and the like is the
                # same page: following them fills the scan and the report with copies.
                if identity in queued or (urlsplit(link).query and _without_query(link) in paths):
                    continue
                # A page's worth of links per page is plenty; a list without
                # limit is memory an unfriendly page can spend for us.
                if len(queue) >= MAX_QUEUE:
                    result.pages_capped = True
                    break
                queued.add(identity)
                paths.add(_without_query(link))
                queue.append(link)
        return result

    def _read_robots(self, base_url: str, result: CrawlResult) -> RobotFileParser:
        robots = RobotFileParser()
        answer = self._fetch(urljoin(base_url, "/robots.txt"), MAX_ROBOTS_BYTES)
        result.robots_status = answer.status
        text = None
        if answer.status == 200:
            text, result.robots_unreadable = read_robots_answer(answer)
        result.robots_text = text
        self._robots_lines = (text or "").splitlines()
        robots.parse(self._robots_lines)
        return robots

    def _read_sitemaps(self, base_url: str, result: CrawlResult, deadline: float) -> list[str]:
        named = [
            line.split(":", 1)[1].strip()
            for line in (result.robots_text or "").splitlines()
            if line.lower().startswith("sitemap:")
        ]
        declared = [url for url in named if _is_on_site(base_url, url)]
        pages: list[str] = []
        seen_pages: set[str] = set()
        # A sitemap named on another host (a CDN, a subdomain) is one we do not
        # read, not one the site does not have.
        silent_declared = len(declared) < len(named)
        for start in declared:
            found, silent = self._read_sitemap_tree(base_url, start, pages, seen_pages, deadline)
            silent_declared = silent_declared or silent
            if found:
                result.sitemap_urls.append(start)
        if result.sitemap_urls:
            result.sitemap_uncertain = silent_declared
            return pages
        # None of the named ones answered with a sitemap (a stale line is common):
        # the usual addresses are still worth a look before saying there is none.
        silent_guesses = False
        for path in COMMON_SITEMAPS:
            start = urljoin(base_url, path)
            found, silent = self._read_sitemap_tree(base_url, start, pages, seen_pages, deadline)
            if found:
                result.sitemap_urls.append(start)
                # Silence in the tree that answered still leaves its list uncertain,
                # and so does a named one we could not read: it may be the real one.
                result.sitemap_uncertain = silent or silent_declared
                return pages
            silent_guesses = silent_guesses or silent
        result.sitemap_uncertain = silent_declared or silent_guesses
        return pages

    def _read_sitemap_tree(self, base_url, start, pages, seen_pages, deadline) -> tuple[bool, bool]:
        """(found a sitemap, met silence on the way). Silence: the list may be short."""
        pending, seen, found_any, silent, opened = [start], set(), False, False, 0
        while pending and self._clock() < deadline:
            if opened >= MAX_CHILD_SITEMAPS:
                silent = True  # more children than one scan opens
                break
            url = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            opened += 1
            answer = self._fetch(url, MAX_SITEMAP_BYTES)
            if answer.status != 200:
                # 404 is an answer. Silence, a server error or a firewall is not.
                if answer.status is None or answer.status == 403 or answer.status >= 500:
                    silent = True
                continue
            if not answer.complete:
                silent = True
                continue
            listed, children = parse_sitemap(answer.body)
            if not listed and not children:
                continue
            found_any = True
            if children or any(marker in answer.body for marker in RICH_SITEMAP_MARKERS):
                self._sitemap_rich = True
            pending.extend(child for child in children if _is_on_site(base_url, child))
            for page in listed:
                identity = _identity(page)
                if (
                    len(pages) < MAX_SITEMAP_URLS
                    and identity not in seen_pages
                    and _is_on_site(base_url, page)
                ):
                    seen_pages.add(identity)
                    pages.append(page)
        if pending and self._clock() >= deadline:
            silent = True
        return found_any, silent

    def _read_page(
        self, url: str, *, robots: RobotFileParser, in_sitemap: bool, always: bool = False
    ) -> tuple[ScannedPage, list[str]] | None:
        """The page and its links; None for a file that turned out not to be a page."""
        # Google's own reading, longest rule first, not the standard library's first match.
        blocked = not google_allows(self._robots_lines, url)
        if blocked and not always:
            return ScannedPage(url=url, robots_blocked=True, in_sitemap=in_sitemap), []
        # A site that keeps this scanner out, and not Google, gets its wish.
        if not always and not robots.can_fetch(ROBOTS_NAME, url):
            return None
        answer = self._fetch(url, MAX_BODY_BYTES)
        redirected = answer.final_url if answer.final_url and _identity(answer.final_url) != _identity(url) else None
        if answer.status != 200:
            return ScannedPage(url=url, status_code=answer.status, redirected_to=redirected, in_sitemap=in_sitemap), []
        if answer.content_type and "html" not in answer.content_type.lower():
            return None
        if not answer.complete and len(answer.body) <= MAX_BODY_BYTES:
            # Cut off by a dropped connection or the clock, not by our size limit:
            # what came may have no title yet, and that is not the page's fault.
            return ScannedPage(url=url, status_code=None, redirected_to=redirected, in_sitemap=in_sitemap), []
        parser = _PageParser()
        try:
            parser.feed(answer.body[:MAX_PARSE_BYTES].decode("utf-8", errors="replace"))
            parser.close()
        except (AssertionError, ValueError, IndexError):
            pass  # what was read before the markup broke down still counts
        return (
            ScannedPage(
                url=url,
                status_code=answer.status,
                redirected_to=redirected,
                title=_field(parser.title),
                meta_description=_field(parser.meta_description),
                h1=_field(parser.h1),
                h1_count=parser.h1_count,
                canonical=_field(_absolute(url, parser.canonical)),
                noindex=parser.noindex or _says_noindex(answer.x_robots),
                robots_blocked=blocked,
                in_sitemap=in_sitemap,
                word_count=parser.words,
            ),
            parser.links,
        )


def read_robots_answer(answer: Fetched) -> tuple[str | None, bool]:
    """(the text, unreadable?). Unreadable: something is there, and proposing from it could lose it."""
    body = answer.body
    head = body.removeprefix(b"\xef\xbb\xbf").lstrip()[:64].lower()
    looks_like_rules = any(
        line.strip().lower().startswith((b"user-agent:", b"disallow:", b"allow:", b"sitemap:"))
        for line in body[:4096].splitlines()
    )
    if head.startswith(b"<!--") and looks_like_rules:
        # A cache plugin's comment on top of real rules: a file, but not one to rebuild.
        return None, True
    if head.startswith((b"<!doctype", b"<html", b"<!--")) or ("html" in answer.content_type.lower() and not looks_like_rules):
        # A CMS's page answering 200 at this address: there is no such file. A real
        # file served with the wrong type is still a file; Google reads it as one.
        return None, False
    # A PHP warning on top of a real file, or only part of one.
    if not answer.complete or len(body) > MAX_ROBOTS_BYTES or b"<" in body[:512].split(b"\n", 1)[0]:
        return None, True
    try:
        # utf-8-sig: a BOM from a Windows editor must not hide the first rule.
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, True
    return (None, True) if "\x00" in text else (text, False)


def _field(value: str | None) -> str | None:
    """As much of a page's text as anyone reads, and nothing the database refuses."""
    if value is None:
        return None
    return value.replace("\x00", "")[:MAX_FIELD_CHARACTERS] or None


def _is_on_site(base_url: str, url: str) -> bool:
    if "\x00" in url:
        return False
    try:
        parts = urlsplit(url)
        # The same host on another port is another service, not this site.
        port_is_fine = parts.port in {None, 80, 443, urlsplit(base_url).port}
        return parts.scheme in {"http", "https"} and port_is_fine and same_site(base_url, url)
    except ValueError:
        return False


def _absolute(base: str, url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urljoin(base, url)
    except ValueError:
        return None
