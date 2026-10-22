"""PornTrex via its HTML search page (KVS tube script).

Results are the cards inside `#list_videos_videos`. Length buckets are path
segments: ten-min (<10), ten-thirty-min (10-30), thirty-all-min (30+);
paging goes after them: /search/<q>/<bucket>/<page>/.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import Query, Video, keywords, parse_count, parse_duration, session, unescape

SORTS = {"new"}
NAME = "porntrex"
BASE = "https://www.porntrex.com"


def duration_bucket(min_sec: int, max_sec: int | None) -> str | None:
    lo, hi = min_sec / 60, (max_sec / 60 if max_sec is not None else None)
    if lo >= 30:
        return "thirty-all-min"
    if lo >= 10 and hi is not None and hi <= 30:
        return "ten-thirty-min"
    if hi is not None and hi <= 10:
        return "ten-min"
    return None


def _abs(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


async def search(q: Query) -> list[Video]:
    path = f"/search/{quote((keywords(q) or 'popular').replace(' ', '-'))}/"
    if bucket := duration_bucket(q.min_sec, q.max_sec):
        path += bucket + "/"
    if q.page > 1:
        path += f"{q.page}/"
    async with session() as s:
        r = await s.get(BASE + path, params={"sort_by": "post_date"} if q.sort == "new" else None)
        r.raise_for_status()
        doc = HTMLParser(r.text)

    root = doc.css_first("#list_videos_videos")
    if root is None:
        return []
    videos = []
    for item in root.css("div.video-item[data-item-id]"):
        vid = item.attributes.get("data-item-id")
        link = item.css_first("p.inf a")
        img = item.css_first("img")
        if not (vid and link):
            continue
        dur = item.css_first("div.durations")
        views = item.css_first("div.viewsthumb")
        rating = None
        for li in item.css("ul.list-unstyled li"):
            if rm := re.search(r"(\d+)%", li.text()):
                rating = float(rm.group(1)) or None  # unrated videos show 0%
        published = item.css_first("ul.list-unstyled li")
        videos.append(Video(
            source=NAME,
            id=vid,
            title=unescape(link.attributes.get("title") or link.text()),
            url=link.attributes.get("href") or f"{BASE}/video/{vid}/",
            embed_url=f"{BASE}/embed/{vid}",
            thumb=_abs(img.attributes.get("data-src") or img.attributes.get("src") or "") if img else "",
            duration=parse_duration(dur.text()) if dur else 0,
            duration_exact=True,
            views=parse_count(views.text().replace("views", "").replace(" ", "")) if views else None,
            rating=rating,
            published=published.text().strip() if published and "%" not in published.text() else "",
        ))
    return videos
