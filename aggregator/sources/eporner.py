"""Eporner via its public JSON API (https://www.eporner.com/api/v2/).

Exact lengths in seconds and a keyword list per video. No server-side length
filter and no uploader; actor/channel/tags are folded into the query.
"""
from __future__ import annotations

import json

from .base import Query, Video, keywords, session

SORTS = {"new"}
NAME = "eporner"
API = "https://www.eporner.com/api/v2/video/search/"
PER_PAGE = 50


async def search(q: Query) -> list[Video]:
    kw = keywords(q)
    params = {
        "query": kw or "all", "page": q.page, "per_page": PER_PAGE, "format": "json",
        "thumbsize": "big", "order": "latest" if q.sort == "new" else "most-popular" if kw else "top-weekly",
    }
    async with session() as s:
        r = await s.get(API, params=params)
        r.raise_for_status()
        data = json.loads(r.content)  # decode as UTF-8 regardless of the response charset

    videos = []
    for v in data.get("videos", []) or []:
        vid = v.get("id")
        if not vid:
            continue
        thumb = (v.get("default_thumb") or {}).get("src", "")
        try:
            rating = float(v.get("rate") or 0) * 20 or None  # API rates 0-5
        except ValueError:
            rating = None
        videos.append(Video(
            source=NAME,
            id=vid,
            title=v.get("title", ""),
            url=v.get("url", ""),
            embed_url=v.get("embed") or f"https://www.eporner.com/embed/{vid}/",
            thumb=thumb,
            duration=int(v.get("length_sec") or 0),
            duration_exact=True,
            views=int(v["views"]) if str(v.get("views", "")).isdigit() else None,
            rating=rating,
            tags=[t.strip() for t in (v.get("keywords") or "").split(",") if t.strip()][:12],
            published=v.get("added", ""),
        ))
    return videos
