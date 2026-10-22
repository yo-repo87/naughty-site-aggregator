"""Shared types and helpers for source adapters."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, asdict

from curl_cffi.requests import AsyncSession

# Browser TLS/HTTP2 fingerprint. SpankBang sits behind Cloudflare and rejects
# plain HTTP clients; impersonating Chrome gets the normal page.
IMPERSONATE = "chrome"
TIMEOUT = 20


@dataclass
class Query:
    text: str = ""
    actor: str = ""
    channel: str = ""
    tags: list[str] = field(default_factory=list)
    min_sec: int = 0
    max_sec: int | None = None  # None = no upper bound
    page: int = 1               # 1-based
    sort: str = ""              # "" = the site's own relevance, "new" = newest first (sites listing it in SORTS)


@dataclass
class Video:
    source: str
    id: str
    title: str
    url: str
    embed_url: str
    thumb: str
    duration: int               # seconds
    duration_exact: bool        # False when the site only reports whole minutes
    preview: str = ""           # short hover-preview mp4, if the site exposes one
    thumb_alt: str = ""         # backup thumbnail if the first fails to load
    views: int | None = None
    rating: float | None = None  # 0-100
    uploader: str = ""          # channel / studio / model / user that posted it
    uploader_url: str = ""
    actors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    published: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def session() -> AsyncSession:
    return AsyncSession(impersonate=IMPERSONATE, timeout=TIMEOUT)


def keywords(q: Query) -> str:
    """Sites without structured actor/channel/tag search get them folded into the keyword query."""
    return " ".join(p for p in [q.text, q.actor, q.channel, *q.tags] if p).strip()


_DUR_PARTS = re.compile(r"(\d+)\s*(h|hr|hours?|m|min|mins|minutes?|s|sec|secs|seconds?)\b", re.I)


def parse_duration(s: str) -> int:
    """Parse '11:34', '1:02:03', '1 h 38 min', '45 sec', '16m', '1h 5m' into seconds."""
    s = s.strip()
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", s):
        total = 0
        for part in s.split(":"):
            total = total * 60 + int(part)
        return total
    total = 0
    for num, unit in _DUR_PARTS.findall(s):
        u = unit[0].lower()
        total += int(num) * (3600 if u == "h" else 60 if u == "m" else 1)
    return total


def parse_count(s: str) -> int | None:
    """'1.2M' / '300' / '45K' / '5,122,487' -> int."""
    s = s.strip().replace(",", "")
    m = re.fullmatch(r"([\d.]+)\s*([KMB]?)", s, re.I)
    if not m:
        return None
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2).upper()]
    return int(float(m.group(1)) * mult)


def unescape(s: str) -> str:
    return html.unescape(s).strip()
