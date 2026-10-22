"""EMPFlix — optional site plug-in (HTML search; uploader, views, rating, hover previews)."""
from __future__ import annotations

from aggregator.sources import flix
from aggregator.sources.base import Query, Video

NAME = "empflix"
LABEL = "EMPFlix"
DESCRIPTION = "Adds EMPFlix to the searched sites (length filter, uploader, views, rating, previews)."
COLOR, INK = "#0284c7", "#fff"
DOMAINS = ("empflix.com",)
BASE = "https://www.empflix.com"


async def search(q: Query) -> list[Video]:
    return await flix.search(q, name=NAME, base=BASE, player="https://player.empflix.com/video/{id}")
