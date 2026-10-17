"""Search API + static UI.

The server only fetches metadata (title, length, thumbnail, links). Videos
themselves are never downloaded or proxied: the UI links to the source site
or loads the site's own embed player.

Fetch strategy: each enabled source is queried for `pages` consecutive result
pages in parallel, using the site's own coarse length filter where one exists.
Results are cached briefly so the UI can re-filter and paginate cheaply.
Precise filtering (slider, excludes) happens in the browser.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query as Q
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import directory
from .auth import db as auth_db, routes as auth_routes, web as auth_web
from .auth.config import settings as auth_settings
from .sources import SOURCES
from .sources.base import Query, Video, session

STATIC = Path(__file__).resolve().parent.parent / "static"
CACHE_TTL = 600
_cache: dict[tuple, tuple[float, list[Video]]] = {}

log = logging.getLogger("aggregator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Accounts are optional: without DATABASE_URL / DATA_KEY / INDEX_KEY the app
    # runs search-only and the UI shows no sign-in.
    if auth_settings.enabled:
        await auth_db.connect()
        log.warning("accounts enabled (db TLS: %s)", auth_settings.db_ssl)
    refresher = asyncio.create_task(directory.keep_fresh())
    yield
    refresher.cancel()
    await auth_db.close()


app = FastAPI(title="Naughty Site Aggregator", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
auth_web.install(app)
app.include_router(auth_routes.router)


async def _fetch_page(name: str, q: Query) -> list[Video]:
    key = (name, q.text, q.actor, q.channel, tuple(q.tags), q.min_sec, q.max_sec, q.page)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    videos = await SOURCES[name].search(q)
    _cache[key] = (time.time(), videos)
    return videos


async def _fetch_source(name: str, base: Query, start_page: int, pages: int) -> list[Video]:
    queries = [Query(**{**base.__dict__, "page": p}) for p in range(start_page, start_page + pages)]
    results = await asyncio.gather(*(_fetch_page(name, q) for q in queries), return_exceptions=True)
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


@app.get("/api/search")
async def search(
    q: str = "",
    actor: str = "",
    channel: str = "",
    tags: str = Q("", description="comma-separated"),
    min_sec: int = 0,
    max_sec: int | None = None,
    sources: str = Q(",".join(SOURCES), description="comma-separated"),
    page: int = Q(1, ge=1, description="batch number; each batch covers `pages` site pages"),
    pages: int = Q(2, ge=1, le=5),
):
    base = Query(
        text=q.strip(),
        actor=actor.strip(),
        channel=channel.strip(),
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        min_sec=max(0, min_sec),
        max_sec=max_sec,
    )
    names = [s for s in sources.split(",") if s in SOURCES]
    start_page = (page - 1) * pages + 1
    results = await asyncio.gather(
        *(_fetch_source(n, base, start_page, pages) for n in names), return_exceptions=True
    )

    status, videos = {}, []
    for name, res in zip(names, results):
        if isinstance(res, BaseException):
            status[name] = {"ok": False, "error": f"{type(res).__name__}: {res}"[:300], "count": 0}
        else:
            status[name] = {"ok": True, "count": len(res)}
            videos.extend(v.to_dict() for v in res)
    return {"videos": videos, "sources": status, "page": page}


@app.get("/api/suggest")
async def suggest(
    kind: str = Q("performer", pattern="^(performer|channel)$"),
    q: str = Q("", max_length=100),
    offset: int = Q(0, ge=0),
    limit: int = Q(30, ge=1, le=100),
):
    """Dropdown entries for the performer / channel boxes; empty q lists everyone by popularity."""
    book = directory.performers if kind == "performer" else directory.channels
    hits, total = book.find(q, offset, limit)
    return {
        "items": [{"name": e.name, "thumb": e.thumb, "videos": e.videos, "sites": e.sites} for e in hits],
        "total": total,
        "ready": bool(book.entries),
    }


# Pornhub's newer "pix-*" thumbnail links only load with a Pornhub Referer,
# which a browser can't send from this site. This relays those thumbnail
# images only (allow-listed CDN host, image content types, size-capped);
# videos are never relayed.
THUMB_HOSTS = (".phncdn.com",)
THUMB_MAX_BYTES = 2_000_000


@app.get("/api/thumb")
async def thumb(u: str):
    host = urlparse(u).hostname or ""
    if urlparse(u).scheme != "https" or not host.endswith(THUMB_HOSTS):
        raise HTTPException(400, "host not allowed")
    try:
        async with session() as s:
            r = await s.get(u, headers={"Referer": "https://www.pornhub.com/"})
    except Exception:
        raise HTTPException(502, "thumbnail unavailable")
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not ctype.startswith("image/") or len(r.content) > THUMB_MAX_BYTES:
        raise HTTPException(502, "thumbnail unavailable")
    return Response(r.content, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})


# Cache busting: the page refers to each static file as /static/x.js?v=<content
# hash>, so a CDN (Cloudflare caches .js/.css for hours) or browser can never
# mix an old script with a new page. Versioned files may be cached forever;
# the page itself is never cached.
_ASSET_RE = re.compile(r'"/static/([\w.-]+)"')
_index_cache: tuple[tuple, str] | None = None


def _asset_version(name: str) -> str:
    return hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:12]


def _index_html() -> str:
    global _index_cache
    files = [STATIC / "index.html", *sorted(STATIC.iterdir())]
    stamp = tuple(f.stat().st_mtime_ns for f in files if f.is_file())
    if _index_cache is None or _index_cache[0] != stamp:  # files changed (dev reload)
        html = (STATIC / "index.html").read_text()
        html = _ASSET_RE.sub(
            lambda m: f'"/static/{m[1]}?v={_asset_version(m[1])}"' if (STATIC / m[1]).is_file() else m[0], html)
        _index_cache = (stamp, html)
    return _index_cache[1]


@app.get("/")
async def index():
    return HTMLResponse(_index_html(), headers={"Cache-Control": "no-cache"})


# Served from the root so the service worker's scope covers the whole app.
@app.get("/sw.js")
async def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


class VersionedStatic(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            versioned = b"v=" in scope.get("query_string", b"")
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
        return response


app.mount("/static", VersionedStatic(directory=STATIC), name="static")
