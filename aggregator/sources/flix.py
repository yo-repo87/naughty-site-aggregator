"""Shared parser for the TNAFlix platform (TNAFlix, EMPFlix), used by the bundled
plug-ins in plugins-available/sources/."""
from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import Query, Video, keywords, parse_count, parse_duration, session, unescape


def duration_bucket(min_sec: int, max_sec: int | None) -> str | None:
    """The site's length filter: short 1-3, medium 3-10, long 10-30, full 30+ minutes.
    Only used when the wanted range fits inside one bucket; the browser filters precisely."""
    lo, hi = min_sec / 60, (max_sec / 60 if max_sec is not None else None)
    if lo >= 30:
        return "full"
    if lo >= 10 and hi is not None and hi <= 30:
        return "long"
    if lo >= 3 and hi is not None and hi <= 10:
        return "medium"
    if hi is not None and hi <= 3:
        return "short"
    return None


def _num(box, icon: str) -> str:
    i = box.css_first(f"i.{icon}")
    return i.parent.text(strip=True) if i and i.parent else ""


async def search(q: Query, *, name: str, base: str, player: str) -> list[Video]:
    params: dict[str, str | int] = {"what": keywords(q) or "popular"}
    if q.page > 1:
        params["page"] = q.page
    if d := duration_bucket(q.min_sec, q.max_sec):
        params["d"] = d
    async with session() as s:
        r = await s.get(base + "/search", params=params)
        r.raise_for_status()
        doc = HTMLParser(r.text)

    videos = []
    for box in doc.css("div.video-list div[data-vid]"):
        vid = box.attributes.get("data-vid")
        thumb_a, title_a, img = box.css_first("a.thumb"), box.css_first("a.video-title"), box.css_first("img")
        if not (vid and thumb_a and title_a):
            continue
        dur = box.css_first(".video-duration")
        uploader = box.css_first("a.badge-video-info")
        rating = _num(box, "icon-thumb-up").rstrip("%")
        videos.append(Video(
            source=name, id=vid, title=unescape(title_a.text()),
            url=thumb_a.attributes.get("href") or "", embed_url=player.format(id=vid),
            thumb=(img.attributes.get("src") or "") if img else "",
            duration=parse_duration(dur.text()) if dur else 0, duration_exact=True,
            preview=thumb_a.attributes.get("data-trailer") or "",
            views=parse_count(_num(box, "icon-eye")),
            rating=float(rating) if rating.isdigit() else None,
            uploader=unescape(uploader.text()) if uploader else "",
        ))
    return videos
