"""HQPorner via its HTML search page (HD-only site).

Titles are often "artistic" and don't contain the search word: the site's
search matches its own tags, so results are still relevant. Lengths are exact
("40m 42s"); no views, uploader or length filter. The player is a per-video
third-party iframe, so there is no embed URL and the UI opens the site.
"""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import Query, Video, keywords, parse_duration, session, unescape

NAME = "hqporner"
BASE = "https://hqporner.com"


def _abs(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


async def search(q: Query) -> list[Video]:
    path, params = "/", {}
    if kw := keywords(q):
        params["q"] = kw
        if q.page > 1:
            params["p"] = q.page
    else:
        # Browsing without keywords: this week's top videos.
        path = "/top/week" + (f"/{q.page}" if q.page > 1 else "")
    async with session() as s:
        r = await s.get(BASE + path, params=params)
        r.raise_for_status()
        doc = HTMLParser(r.text)

    videos = []
    for card in doc.css("section.box.feature"):
        link = card.css_first("h3.meta-data-title a[href^='/hdporn/']")
        if not link:
            continue
        href = link.attributes.get("href") or ""
        m = re.match(r"/hdporn/(\d+)-", href)
        if not m:
            continue
        img = card.css_first("img")
        dur = card.css_first("span.fa-clock-o")
        videos.append(Video(
            source=NAME,
            id=m.group(1),
            title=unescape(link.text()).capitalize(),
            url=BASE + href,
            embed_url="",
            thumb=_abs(img.attributes.get("src") or "") if img else "",
            duration=parse_duration(dur.text()) if dur else 0,
            duration_exact=True,
        ))
    return videos
