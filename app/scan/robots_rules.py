"""robots.txt read the way Google reads it: by the group that applies to Googlebot.

A crawler obeys the one group naming it, and only falls back to "*" when none
does. So "User-agent: Googlebot / Allow: /" beside "User-agent: * / Disallow: /"
keeps every other robot out and lets Google in, and must not be called closed,
let alone "fixed" by opening the site to everyone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

GOOGLE = "googlebot"


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    # (line number, directive, value), in the file's order.
    rules: list[tuple[int, str, str]] = field(default_factory=list)


def parse_groups(lines: list[str]) -> list[Group]:
    groups: list[Group] = []
    current: Group | None = None
    for number, raw in enumerate(lines):
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        name = name.lower()
        if name == "user-agent":
            # A user-agent after rules starts a new group; one after another
            # user-agent joins the same group.
            if current is None or current.rules:
                current = Group()
                groups.append(current)
            # Google reads only the product token: "Googlebot/2.1" is "googlebot".
            token = re.match(r"[a-z_*-]+", value.lower())
            current.agents.append(token.group(0) if token else value.lower())
        elif name in {"allow", "disallow"} and current is not None:
            current.rules.append((number, name, value))
    return groups


def groups_for_google(groups: list[Group]) -> list[Group]:
    named = [group for group in groups if GOOGLE in group.agents]
    return named or [group for group in groups if "*" in group.agents]


def blocking_lines(lines: list[str]) -> list[int]:
    """The "Disallow: /" lines that keep Google out of the whole site; none if anything is let in.

    An Allow with a path is a site closed on purpose but for that part (a
    whitelist), not a leftover: it is left alone.
    """
    rules = [rule for group in groups_for_google(parse_groups(lines)) for rule in group.rules]
    blocking = [number for number, name, value in rules if name == "disallow" and value == "/"]
    opens = any(name == "allow" and value for _, name, value in rules)
    return [] if opens else blocking


def blocks_everything(text: str | None) -> bool:
    return bool(blocking_lines((text or "").splitlines()))


def _matches(pattern: str, path: str) -> bool:
    regex = "".join(".*" if char == "*" else re.escape(char) for char in pattern.rstrip("$"))
    return re.match(regex + ("$" if pattern.endswith("$") else ""), path) is not None


def google_allows(lines: list[str], url: str) -> bool:
    """May Googlebot read this address? The longest matching rule wins, Allow on a tie.

    Not the first match in the file's order: "Disallow: /shop/" then
    "Allow: /shop/products/" leaves the products open to Google.
    """
    parts = urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    best: tuple[int, bool] | None = None
    for group in groups_for_google(parse_groups(lines)):
        for _, name, value in group.rules:
            if not value or not _matches(value, path):
                continue
            candidate = (len(value), name == "allow")
            if best is None or candidate > best:
                best = candidate
    return best is None or best[1]
