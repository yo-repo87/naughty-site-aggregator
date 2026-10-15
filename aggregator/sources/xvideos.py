"""XVideos via its HTML search page (no public API).

The site has coarse server-side duration buckets (`durf`), which we pick to
narrow results before precise filtering. Listings report whole minutes only
and carry no tag/performer data, so actor and tags are folded into keywords.
"""
from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import Query, Video, keywords, parse_duration, session, unescape

NAME = "xvideos"
BASE = "https://www.xvideos.com"


def duration_bucket(min_sec: int, max_sec: int | None) -> str:
    lo, hi = min_sec / 60, (max_sec / 60 if max_sec is not None else None)
    if lo >= 20:
        return "20min_more"
    if lo >= 10:
        return "10-20min" if hi is not None and hi <= 20 else "10min_more"
    if lo >= 3 and hi is not None and hi <= 10:
        return "3-10min"
    if lo >= 1 and hi is not None and hi <= 3:
        return "1-3min"
    return "allduration"


async def search(q: Query) -> list[Video]:
    params = {"durf": duration_bucket(q.min_sec, q.max_sec)}
    path = "/"
    if kw := keywords(q):
        params["k"] = kw
        if q.page > 1:
            params["p"] = q.page - 1  # site pages are 0-based
    elif q.page > 1:
        # An empty `k` is a 500 on XVideos; browse the homepage, then /new/N.
        path = f"/new/{q.page - 1}"
    async with session() as s:
        r = await s.get(BASE + path, params=params)
        r.raise_for_status()
        doc = HTMLParser(r.text)

    videos = []
    for block in doc.css("div.thumb-block[data-eid]"):
        eid = block.attributes.get("data-eid")
        num_id = block.attributes.get("data-id")
        link = block.css_first("p.title a")
        img = block.css_first("div.thumb img")
        dur = block.css_first("p.metadata span.duration") or block.css_first("span.duration")
        if not (eid and num_id and link and img):
            continue
        uploader = block.css_first("p.metadata span.name")
        up_link = block.css_first("p.metadata a[href]")
        videos.append(Video(
            source=NAME,
            id=eid,
            title=unescape(link.attributes.get("title") or link.text()),
            url=BASE + (link.attributes.get("href") or ""),
            embed_url=f"{BASE}/embedframe/{num_id}",
            thumb=img.attributes.get("data-src") or img.attributes.get("src") or "",
            preview=img.attributes.get("data-pvv") or "",
            duration=parse_duration(dur.text()) if dur else 0,
            duration_exact=False,
            uploader=uploader.text().strip() if uploader else "",
            uploader_url=BASE + up_link.attributes["href"] if up_link else "",
        ))
    return videos
