"""DrTuber — optional site plug-in (HTML search; rating, hover previews)."""
from __future__ import annotations

import re
from urllib.parse import quote

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from aggregator.sources.base import Query, Video, keywords, parse_duration, session, unescape

NAME = "drtuber"
LABEL = "DrTuber"
DESCRIPTION = "Adds DrTuber to the searched sites (rating, previews; no length filter on the site)."
COLOR, INK = "#16a34a", "#fff"
DOMAINS = ("drtuber.com",)
BASE = "https://www.drtuber.com"
_ID = re.compile(r"^/video/(\d+)/")


async def search(q: Query) -> list[Video]:
    path = f"/search/videos/{quote(keywords(q) or 'popular')}" + (f"/{q.page}" if q.page > 1 else "")
    async with session() as s:
        r = await s.get(BASE + path)
        r.raise_for_status()
        doc = HTMLParser(r.text)

    videos = []
    for a in doc.css("#search_results a.ch-video"):
        href = a.attributes.get("href") or ""
        m = _ID.match(href)
        img = a.css_first("img")
        if not (m and img):
            continue
        title = a.css_first("span em")
        dur = a.css_first(".time_thumb em")
        rate = a.css_first(".rate_thumb em em") or a.css_first(".rate_thumb em")
        rating = (rate.text(strip=True).rstrip("%") if rate else "")
        videos.append(Video(
            source=NAME, id=m.group(1),
            # the search highlights matched words with <i> tags, which leaves double spaces
            title=" ".join(unescape(title.text() if title else img.attributes.get("alt") or "").split()),
            url=BASE + href, embed_url=f"{BASE}/embed/{m.group(1)}",
            thumb=img.attributes.get("src") or "", preview=img.attributes.get("data-webm") or "",
            duration=parse_duration(dur.text()) if dur else 0, duration_exact=True,
            rating=float(rating) if rating.isdigit() else None,
        ))
    return videos
