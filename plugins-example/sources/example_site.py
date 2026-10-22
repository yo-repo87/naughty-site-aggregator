"""Template for a site plug-in. Copy to plugins/sources/<name>.py and fill in search().

The app calls search(query) for each page it needs and shows whatever Video objects come back.
"""
from __future__ import annotations

from urllib.parse import quote

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from aggregator.sources.base import Query, Video, keywords, parse_count, parse_duration, session, unescape

NAME = "examplesite"            # unique id, letters/digits/_ only; same as a built-in name replaces it
LABEL = "ExampleSite"           # badge text
COLOR, INK = "#0ea5e9", "#000"  # badge background / text colour
DOMAINS = ("example.com",)      # video pages on these domains may be sent to MeTube etc.
SORTS = set()                   # {"new"} if search() honours query.sort == "new" (newest first)
BASE = "https://www.example.com"


async def search(q: Query) -> list[Video]:
    # q.text, q.actor, q.channel, q.tags, q.min_sec, q.max_sec, q.page (1-based), q.sort
    async with session() as s:  # Chrome-like TLS fingerprint, 20 s timeout
        r = await s.get(f"{BASE}/search/{quote(keywords(q) or 'popular')}/{q.page}")
        r.raise_for_status()
    doc = HTMLParser(r.text)
    videos = []
    for item in doc.css("div.video-item"):
        link, img, dur = item.css_first("a.title"), item.css_first("img"), item.css_first(".duration")
        if not (link and img):
            continue
        vid = link.attributes.get("href", "").rstrip("/").split("/")[-1]
        videos.append(Video(
            source=NAME, id=vid, title=unescape(link.text()), url=BASE + link.attributes.get("href", ""),
            embed_url=f"{BASE}/embed/{vid}",       # "" if the site has no embeddable player
            thumb=img.attributes.get("data-src") or img.attributes.get("src") or "",
            duration=parse_duration(dur.text()) if dur else 0, duration_exact=True,
            views=parse_count(item.css_first(".views").text()) if item.css_first(".views") else None,
        ))
    return videos
