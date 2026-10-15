"""RedTube via its public JSON API (same shape as Pornhub's webmasters API).

tags[] is honoured; stars[] is ignored by the API, so performer and channel
are folded into the search text. No length filter: lengths are exact, so the
app filters after fetch.
"""
from __future__ import annotations

from .base import Query, Video, parse_duration, session

NAME = "redtube"
API = "https://api.redtube.com/"


async def search(q: Query) -> list[Video]:
    text = " ".join(p for p in [q.text, q.actor, q.channel] if p)
    params: list[tuple[str, str | int]] = [
        ("data", "redtube.Videos.searchVideos"), ("output", "json"), ("thumbsize", "big"), ("page", q.page),
    ]
    if text:
        params.append(("search", text))
    for t in q.tags:
        params.append(("tags[]", t))
    if not text and not q.tags:
        params.append(("ordering", "mostviewed"))

    async with session() as s:
        r = await s.get(API, params=params)
        r.raise_for_status()
        data = r.json()

    videos = []
    for item in data.get("videos", []) or []:
        v = item.get("video") or {}
        vid = str(v.get("video_id") or "")
        if not vid:
            continue
        thumbs = v.get("thumbs") or []
        try:
            rating = float(v.get("rating") or 0) or None
        except ValueError:
            rating = None
        videos.append(Video(
            source=NAME,
            id=vid,
            title=v.get("title", ""),
            url=v.get("url") or f"https://www.redtube.com/{vid}",
            embed_url=v.get("embed_url") or f"https://embed.redtube.com/?id={vid}",
            thumb=(thumbs[0].get("src") if thumbs else None) or v.get("thumb") or v.get("default_thumb", ""),
            thumb_alt=v.get("default_thumb", ""),
            duration=parse_duration(v.get("duration", "0:00")),
            duration_exact=True,
            views=int(v["views"]) if str(v.get("views", "")).isdigit() else None,
            rating=rating,
            tags=[t.get("tag_name", "") for t in v.get("tags") or [] if t.get("tag_name")],
            published=v.get("publish_date", ""),
        ))
    return videos
