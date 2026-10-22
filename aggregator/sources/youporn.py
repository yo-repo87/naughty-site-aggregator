"""YouPorn via its HTML search page (Aylo platform, see aylo_html)."""
from __future__ import annotations

from . import aylo_html
from .base import Query, Video

SORTS = {"new"}
NAME = "youporn"
BASE = "https://www.youporn.com"


async def search(q: Query) -> list[Video]:
    return await aylo_html.search(
        q, name=NAME, base=BASE, path="/search/date/" if q.sort == "new" else "/search/", query_param="query", embed=BASE + "/embed/{id}/"
    )
