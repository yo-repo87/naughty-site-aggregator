"""Tube8 via its HTML search page (Aylo platform, see aylo_html)."""
from __future__ import annotations

from . import aylo_html
from .base import Query, Video

NAME = "tube8"
BASE = "https://www.tube8.com"


async def search(q: Query) -> list[Video]:
    return await aylo_html.search(
        q, name=NAME, base=BASE, path="/searches.html", query_param="q", embed=BASE + "/embed/{id}/"
    )
