"""YouJizz via its HTML search page.

No length filter, no uploader, and no working embed player (the embed URL
returns 500), so its videos open on the site instead of the in-page player.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import Query, Video, keywords, parse_count, parse_duration, session, unescape

NAME = "youjizz"
BASE = "https://www.youjizz.com"


def _abs(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


async def search(q: Query) -> list[Video]:
    term = quote((keywords(q) or "popular").replace(" ", "-"))
    async with session() as s:
        r = await s.get(f"{BASE}/search/{term}-{q.page}.html")
        r.raise_for_status()
        doc = HTMLParser(r.text)

    videos = []
    for item in doc.css("div.video-thumb[data-videoid]"):
        vid = item.attributes.get("data-videoid")
        frame = item.css_first("a.frame")
        title = item.css_first(".video-title a")
        img = item.css_first("img")
        if not (vid and frame and title):
            continue
        time_el = item.css_first("span.time")
        views = item.css_first(".format-views")
        dur = re.search(r"[\d:]+", time_el.text()) if time_el else None
        videos.append(Video(
            source=NAME,
            id=vid,
            title=unescape(title.text()),
            url=BASE + (frame.attributes.get("href") or ""),
            embed_url="",
            thumb=_abs(img.attributes.get("data-original") or "") if img else "",
            preview=_abs(frame.attributes.get("data-clip") or ""),
            duration=parse_duration(dur.group(0)) if dur else 0,
            duration_exact=True,
            views=parse_count(views.text()) if views else None,
        ))
    return videos
