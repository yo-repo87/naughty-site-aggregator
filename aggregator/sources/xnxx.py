"""XNXX via its HTML search page (same platform as XVideos).

Length buckets go in the path: /search/<bucket>/<query>/<page>, with pages
0-based. Listings show whole minutes; the uploader is shown only for
channel/creator uploads.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import Query, Video, keywords, parse_count, parse_duration, session, unescape

NAME = "xnxx"
BASE = "https://www.xnxx.com"
_DUR = re.compile(r"(\d+\s*h\s*\d+\s*min|\d+\s*h|\d+\s*min|\d+\s*sec)")


def duration_bucket(min_sec: int, max_sec: int | None) -> str | None:
    lo, hi = min_sec / 60, (max_sec / 60 if max_sec is not None else None)
    if lo >= 20:
        return "20min+"
    if lo >= 10:
        return "10-20min" if hi is not None and hi <= 20 else "10min+"
    if hi is not None and hi <= 10:
        return "0-10min"
    return None


async def search(q: Query) -> list[Video]:
    parts = ["/search"]
    if bucket := duration_bucket(q.min_sec, q.max_sec):
        parts.append(bucket)
    parts.append(quote(keywords(q) or "popular"))
    if q.page > 1:
        parts.append(str(q.page - 1))
    async with session() as s:
        r = await s.get(BASE + "/".join(parts))
        r.raise_for_status()
        doc = HTMLParser(r.text)

    videos = []
    for block in doc.css("div.thumb-block[data-eid]"):
        eid = block.attributes.get("data-eid")
        link = block.css_first(".thumb-under a[title]")
        img = block.css_first("div.thumb img")
        meta = block.css_first("p.metadata")
        if not (eid and link and img):
            continue
        meta_text = meta.text(separator=" ") if meta else ""
        dm = _DUR.search(meta_text.split("%", 1)[-1])  # skip the views/rating part
        views = block.css_first("p.metadata span.right")
        rate = block.css_first("p.metadata span.superfluous")
        rating = None
        if rate and (rm := re.search(r"(\d+)%", rate.text())):
            rating = float(rm.group(1))
        up = block.css_first("div.uploader a")
        up_name = block.css_first("div.uploader span.name")
        videos.append(Video(
            source=NAME,
            id=eid,
            title=unescape(link.attributes.get("title") or link.text()),
            url=BASE + (link.attributes.get("href") or ""),
            embed_url=f"{BASE}/embedframe/{eid}",
            thumb=img.attributes.get("data-src") or img.attributes.get("src") or "",
            preview=img.attributes.get("data-pvv") or "",
            duration=parse_duration(dm.group(1)) if dm else 0,
            duration_exact=False,
            views=parse_count(views.text(deep=False).strip()) if views else None,
            rating=rating,
            uploader=up_name.text().strip() if up_name else "",
            uploader_url=BASE + up.attributes.get("href", "") if up else "",
        ))
    return videos
