"""SpankBang via its HTML search page (no public API, Cloudflare-fronted).

Server-side length filter `d` accepts 10/20/40 (minutes, "or longer").
Listings report whole minutes and no tags, so actor/tags become keywords.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import Query, Video, keywords, parse_count, parse_duration, session, unescape

NAME = "spankbang"
BASE = "https://spankbang.com"


def duration_floor(min_sec: int) -> int | None:
    for d in (40, 20, 10):
        if min_sec >= d * 60:
            return d
    return None


async def search(q: Query) -> list[Video]:
    term = quote_plus(keywords(q) or "trending")
    path = f"/s/{term}/" + (f"{q.page}/" if q.page > 1 else "")
    params = {}
    if (d := duration_floor(q.min_sec)) is not None:
        params["d"] = d
    async with session() as s:
        r = await s.get(BASE + path, params=params)
        r.raise_for_status()
        text = r.text

    # The page opens with a sponsored/trending strip that repeats on every
    # page; real results start at the search-result container.
    start = text.find('data-testid="search-result"')
    doc = HTMLParser(text[start:] if start >= 0 else text)

    videos = []
    for item in doc.css('div[data-testid="video-item"]'):
        num_id = item.attributes.get("data-id")
        a = item.css_first("a[href*='/video/']")
        img = item.css_first("img")
        if not (num_id and a and img):
            continue
        href = a.attributes.get("href") or ""
        m = re.match(r"/([^/]+)/video/", href)
        if not m:
            continue
        code = m.group(1)
        title_a = item.css_first("p a[title]")
        length = item.css_first('[data-testid="video-item-length"]')
        views = item.css_first('[data-testid="views"] span.md\\:text-body-md')
        rates = item.css_first('[data-testid="rates"] span.md\\:text-body-md')
        # The name under the thumbnail is badged as a channel, pornstar or tag.
        badge_a = item.css_first('a[data-testid="title"]')
        badge = item.css_first("[data-badge]")
        kind = badge.attributes.get("data-badge") if badge else "channel"
        badge_name = badge_a.text().strip() if badge_a else ""
        uploader = badge_a if kind != "tag" else None
        source = item.css_first("video source")
        rating = None
        if rates and (rm := re.search(r"(\d+(?:\.\d+)?)%", rates.text())):
            rating = float(rm.group(1))
        videos.append(Video(
            source=NAME,
            id=code,
            title=unescape(title_a.attributes.get("title") if title_a else img.attributes.get("alt", "")),
            url=BASE + href,
            embed_url=f"{BASE}/{code}/embed/",
            thumb=img.attributes.get("src") or "",
            preview=(source.attributes.get("data-src") or "") if source else "",
            duration=parse_duration(length.text()) if length else 0,
            duration_exact=False,
            views=parse_count(views.text()) if views else None,
            rating=rating,
            uploader=uploader.text().strip() if uploader else "",
            uploader_url=BASE + (uploader.attributes.get("href") or "") if uploader else "",
            actors=[badge_name] if kind == "pornstar" and badge_name else [],
            tags=[badge_name.lower()] if kind == "tag" and badge_name else [],
        ))
    return videos
