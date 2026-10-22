"""xHamster via its search page's embedded JSON (`window.initials`).

The page ships its result list as JSON, so no HTML parsing: exact lengths,
uploader ("landing"), views and a preview clip. Length filter is
`min-duration` / `max-duration` in minutes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import quote

from .base import Query, Video, keywords, session

SORTS = {"new"}
NAME = "xhamster"
BASE = "https://xhamster.com"
MARKER = "window.initials="


async def search(q: Query) -> list[Video]:
    params: dict[str, int] = {}
    if q.page > 1:
        params["page"] = q.page
    if q.sort == "new":
        params["sort"] = "newest"
    if q.min_sec >= 60:
        params["min-duration"] = q.min_sec // 60
    if q.max_sec is not None:
        params["max-duration"] = max(q.max_sec // 60, params.get("min-duration", 0) + 1)
    async with session() as s:
        r = await s.get(f"{BASE}/search/{quote(keywords(q) or 'popular')}", params=params)
        r.raise_for_status()
        text = r.text

    i = text.find(MARKER)
    if i < 0:
        raise ValueError("xHamster page layout changed: no window.initials")
    data, _ = json.JSONDecoder().raw_decode(text[i + len(MARKER):])
    items = (data.get("searchResult") or {}).get("videoThumbProps") or []

    videos = []
    for v in items:
        page = v.get("pageURL") or ""
        code = page.rstrip("/").rsplit("-", 1)[-1]
        if not code or v.get("videoType", "video") != "video":
            continue
        landing = v.get("landing") or {}
        created = v.get("created")
        videos.append(Video(
            source=NAME,
            id=code,
            title=v.get("title", ""),
            url=page,
            embed_url=f"{BASE}/embed/{code}",
            thumb=v.get("thumbURL") or v.get("imageURL") or "",
            thumb_alt=v.get("imageURL") or "",
            preview=v.get("trailerFallbackUrl") or v.get("trailerURL") or "",
            duration=int(v.get("duration") or 0),
            duration_exact=True,
            views=v.get("views"),
            uploader=landing.get("name") or "",
            uploader_url=landing.get("link") or "",
            published=datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m-%d") if created else "",
        ))
    return videos
