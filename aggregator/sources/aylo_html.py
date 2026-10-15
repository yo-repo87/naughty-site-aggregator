"""Shared parser for Tube8 and YouPorn search pages (same Aylo site platform).

Cards are `article.video-box[data-video-id]` with the uploader name/type in data
attributes. The search page filters length server-side to the minute with
`min_minutes` / `max_minutes` (the site slider tops out at 60).
"""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import Query, Video, keywords, parse_count, parse_duration, session, unescape

SLIDER_TOP_MIN = 60


def length_params(q: Query) -> dict[str, int]:
    params = {}
    lo = q.min_sec // 60
    if lo > 0:
        params["min_minutes"] = min(lo, SLIDER_TOP_MIN)
    if q.max_sec is not None and q.max_sec // 60 < SLIDER_TOP_MIN:
        params["max_minutes"] = max(q.max_sec // 60, params.get("min_minutes", 0) + 1)
    return params


async def search(q: Query, *, name: str, base: str, path: str, query_param: str, embed: str) -> list[Video]:
    params: dict[str, str | int] = {query_param: keywords(q) or "popular", **length_params(q)}
    if q.page > 1:
        params["page"] = q.page
    async with session() as s:
        r = await s.get(base + path, params=params)
        r.raise_for_status()
        doc = HTMLParser(r.text)

    videos = []
    for box in doc.css("article.video-box[data-video-id]"):
        a = box.css_first("a.video-title-text") or box.css_first("a[href]")
        img = box.css_first("img")
        if not (a and img):
            continue
        href = a.attributes.get("href") or ""
        m = re.search(r"/(\d+)/?$", href)
        if not m:
            continue
        vid = m.group(1)
        dur = box.css_first(".video-duration span")
        up_a = box.css_first("a.author-title-text")
        up_name = unescape(box.attributes.get("data-uploader-name") or (up_a.text() if up_a else ""))
        up_type = box.attributes.get("data-uploader-type") or ""
        actors = [p.text().strip() for p in box.css(".performers-list a") if p.text().strip()]
        if up_type == "pornstar" and up_name and up_name not in actors:
            actors.insert(0, up_name)
        stats = [s.text().replace("Views:", "").replace("Rating:", "").strip() for s in box.css("span.info-views")]
        rating = None
        if len(stats) > 1 and (rm := re.search(r"(\d+(?:\.\d+)?)%", stats[1])):
            rating = float(rm.group(1))
        title_span = a.css_first("span")
        videos.append(Video(
            source=name,
            id=vid,
            title=unescape(box.attributes.get("aria-label") or (title_span.text() if title_span else a.text())),
            url=base + href,
            embed_url=embed.format(id=vid),
            thumb=img.attributes.get("data-src") or img.attributes.get("data-poster") or "",
            preview=img.attributes.get("data-mediabook") or "",
            duration=parse_duration(dur.text()) if dur else 0,
            duration_exact=True,
            views=parse_count(stats[0]) if stats else None,
            rating=rating,
            uploader=up_name,
            uploader_url=base + up_a.attributes.get("href", "") if up_a else "",
            actors=actors,
        ))
    return videos
