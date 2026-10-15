"""Pornhub: public webmasters JSON API + the regular HTML search page, merged.

- The API gives structured search (tags[], stars[]) and returns tags and
  performers per video, but no uploader/channel and no length filter.
- The HTML search page gives the uploader (channel / model / user) and has a
  server-side minimum-length filter, but no tags or performers.

Both run in parallel and are merged by viewkey, so a video found by either
gets every field the other one knows.
"""
from __future__ import annotations

import asyncio

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import Query, Video, keywords, parse_count, parse_duration, session, unescape

NAME = "pornhub"
BASE = "https://www.pornhub.com"
API = BASE + "/webmasters/search"


def _embed(key: str) -> str:
    return f"{BASE}/embed/{key}"


async def _api(q: Query) -> list[Video]:
    params: list[tuple[str, str | int]] = [("page", q.page), ("thumbsize", "large_hd")]
    text = " ".join(p for p in [q.text, q.channel] if p)
    if text:
        params.append(("search", text))
    if q.actor:
        params.append(("stars[]", q.actor))
    for t in q.tags:
        params.append(("tags[]", t))
    if not text and not q.actor and not q.tags:
        params.append(("ordering", "mostviewed"))

    async with session() as s:
        r = await s.get(API, params=params)
        r.raise_for_status()
        data = r.json()

    videos = []
    for v in data.get("videos", []) or []:
        key = v.get("video_id")
        if not key:
            continue
        thumbs = v.get("thumbs") or []
        try:
            rating = float(v.get("rating") or 0) or None
        except ValueError:
            rating = None
        videos.append(Video(
            source=NAME,
            id=key,
            title=v.get("title", ""),
            url=v.get("url") or f"{BASE}/view_video.php?viewkey={key}",
            embed_url=_embed(key),
            thumb=(thumbs[0].get("src") if thumbs else None) or v.get("default_thumb", ""),
            duration=parse_duration(v.get("duration", "0:00")),
            duration_exact=True,
            views=int(v["views"]) if str(v.get("views", "")).isdigit() else None,
            rating=rating,
            actors=[p.get("pornstar_name", "") for p in v.get("pornstars") or [] if p.get("pornstar_name")],
            tags=[t.get("tag_name", "") for t in v.get("tags") or [] if t.get("tag_name")],
            published=v.get("publish_date", ""),
        ))
    return videos


def _min_duration(min_sec: int) -> int | None:
    for d in (30, 20, 10):
        if min_sec >= d * 60:
            return d
    return None


async def _html(q: Query) -> list[Video]:
    params: dict[str, str | int] = {"search": keywords(q) or "popular", "page": q.page}
    if (d := _min_duration(q.min_sec)) is not None:
        params["min_duration"] = d
    async with session() as s:
        r = await s.get(BASE + "/video/search", params=params)
        r.raise_for_status()
        doc = HTMLParser(r.text)

    # Only the main result list; the page also carries menus and "hottest" strips.
    root = doc.css_first("ul#videoSearchResult")
    if root is None:
        return []
    videos = []
    for li in root.css("li.pcVideoListItem[data-video-vkey]"):
        key = li.attributes.get("data-video-vkey")
        title_a = li.css_first("span.title a")
        img = li.css_first("img")
        dur = li.css_first("var.duration")
        if not (key and title_a and img):
            continue
        up = li.css_first(".usernameWrap a")
        views = li.css_first("span.views var")
        rating = li.css_first(".rating-container .value")
        rating_val = None
        if rating:
            try:
                rating_val = float(rating.text().strip().rstrip("%"))
            except ValueError:
                pass
        videos.append(Video(
            source=NAME,
            id=key,
            title=unescape(title_a.attributes.get("title") or title_a.text()),
            url=f"{BASE}/view_video.php?viewkey={key}",
            embed_url=_embed(key),
            thumb=img.attributes.get("src") or img.attributes.get("data-mediumthumb") or "",
            preview=img.attributes.get("data-mediabook") or "",
            duration=parse_duration(dur.text()) if dur else 0,
            duration_exact=True,
            views=parse_count(views.text()) if views else None,
            rating=rating_val,
            uploader=up.text().strip() if up else "",
            uploader_url=BASE + (up.attributes.get("href") or "") if up else "",
        ))
    return videos


async def search(q: Query) -> list[Video]:
    api_res, html_res = await asyncio.gather(_api(q), _html(q), return_exceptions=True)
    if isinstance(api_res, BaseException) and isinstance(html_res, BaseException):
        raise api_res
    api_list = [] if isinstance(api_res, BaseException) else api_res
    html_list = [] if isinstance(html_res, BaseException) else html_res

    by_key = {v.id: v for v in html_list}
    merged = []
    for v in api_list:
        h = by_key.pop(v.id, None)
        if h:
            v.uploader, v.uploader_url = h.uploader, h.uploader_url
            v.preview = v.preview or h.preview
            v.thumb_alt = h.thumb
        merged.append(v)
    # Interleave HTML-only finds after the API ones, keeping site order.
    merged.extend(v for v in html_list if v.id in by_key)
    return merged
