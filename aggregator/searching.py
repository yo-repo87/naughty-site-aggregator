"""Fetching from the sites, shared by /api/search, the favorites feed and alerts.

Each page result is cached briefly so the UI can re-filter and paginate cheaply,
and every request is recorded for the admin page's site health.
"""
from __future__ import annotations

import asyncio
import time

from . import health
from .sources import SOURCES
from .sources.base import Query, Video

CACHE_TTL = 600
_cache: dict[tuple, tuple[float, list[Video]]] = {}


async def fetch_page(name: str, q: Query) -> list[Video]:
    key = (name, q.text, q.actor, q.channel, tuple(q.tags), q.min_sec, q.max_sec, q.page, q.sort)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    started = time.monotonic()
    try:
        videos = await SOURCES[name].search(q)
    except Exception as e:
        health.record(name, False, (time.monotonic() - started) * 1000, f"{type(e).__name__}: {e}")
        raise
    health.record(name, True, (time.monotonic() - started) * 1000)
    if len(_cache) > 2000:  # drop expired entries now and then
        now = time.time()
        for k in [k for k, (t, _) in _cache.items() if now - t >= CACHE_TTL]:
            _cache.pop(k, None)
    _cache[key] = (time.time(), videos)
    return videos


async def fetch_source(name: str, base: Query, start_page: int, pages: int) -> list[Video]:
    queries = [Query(**{**base.__dict__, "page": p}) for p in range(start_page, start_page + pages)]
    results = await asyncio.gather(*(fetch_page(name, q) for q in queries), return_exceptions=True)
    ok = [r for r in results if not isinstance(r, BaseException)]
    if not ok:  # every page failed: surface the first error
        raise results[0]
    seen, out = set(), []
    for page in ok:
        for v in page:
            if v.id not in seen:
                seen.add(v.id)
                out.append(v)
    return out


def supports_new(name: str) -> bool:
    return "new" in getattr(SOURCES.get(name), "SORTS", ())
