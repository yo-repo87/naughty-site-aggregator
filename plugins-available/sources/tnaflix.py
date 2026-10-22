"""TNAFlix — optional site plug-in (HTML search; uploader, views, rating, hover previews)."""
from __future__ import annotations

from aggregator.sources import flix
from aggregator.sources.base import Query, Video

NAME = "tnaflix"
LABEL = "TNAFlix"
DESCRIPTION = "Adds TNAFlix to the searched sites (length filter, uploader, views, rating, previews)."
COLOR, INK = "#f97316", "#000"
DOMAINS = ("tnaflix.com",)
BASE = "https://www.tnaflix.com"


async def search(q: Query) -> list[Video]:
    return await flix.search(q, name=NAME, base=BASE, player="https://player.tnaflix.com/video/{id}")
