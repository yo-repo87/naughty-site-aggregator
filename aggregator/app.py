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
from contextlib import asynccontextmanager
from pathlib import Path

from urllib.parse import urlparse

from fastapi import Body, FastAPI, HTTPException, Query as Q
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import directory, feed, plugins
from .auth import admin as auth_admin, alerts as auth_alerts, db as auth_db, downloads as auth_downloads, routes as auth_routes, send as auth_send, web as auth_web
from .auth.config import settings as auth_settings
from .searching import fetch_source
from .sources import SOURCES
from .sources.base import Query, session

STATIC = Path(__file__).resolve().parent.parent / "static"

log = logging.getLogger("aggregator")
plugins.load()  # before anything reads SOURCES (the search endpoint's default list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Accounts are optional: without DATABASE_URL / DATA_KEY / INDEX_KEY the app
    # runs search-only and the UI shows no sign-in.
    if auth_settings.enabled:
        await auth_db.connect()
        await auth_send.migrate_metube()
        await auth_admin.load_app_settings()
        log.warning("accounts enabled (db TLS: %s)", auth_settings.db_ssl)
    tasks = [asyncio.create_task(directory.keep_fresh()), asyncio.create_task(feed.worker())]
    if auth_settings.enabled:
        tasks.append(asyncio.create_task(auth_alerts.loop()))
        tasks.append(asyncio.create_task(auth_downloads.loop()))
    yield
    for t in tasks:
        t.cancel()
    await auth_db.close()


app = FastAPI(title="Naughty Site Aggregator", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
auth_web.install(app)
app.include_router(auth_routes.router)
for module in (auth_send, auth_alerts, auth_admin, auth_downloads):
    app.include_router(module.router)


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
    sort: str = Q("", pattern="^(|new)$", description="new = newest first where the site supports it"),
):
    base = Query(
        text=q.strip(),
        actor=actor.strip(),
        channel=channel.strip(),
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        min_sec=max(0, min_sec),
        max_sec=max_sec,
        sort=sort,
    )
    names = [s for s in sources.split(",") if s in SOURCES]
    start_page = (page - 1) * pages + 1
    results = await asyncio.gather(
        *(fetch_source(n, base, start_page, pages) for n in names), return_exceptions=True
    )

    status, videos = {}, []
    for name, res in zip(names, results):
        if isinstance(res, BaseException):
            status[name] = {"ok": False, "error": f"{type(res).__name__}: {res}"[:300], "count": 0}
        else:
            status[name] = {"ok": True, "count": len(res)}
            videos.extend(v.to_dict() for v in res)
    return {"videos": videos, "sources": status, "page": page}


@app.get("/api/sources")
async def sources_info():
    """Every source with what it supports; plug-in sources also bring their badge label and colours."""
    return {"sources": [{"name": n, "sorts": sorted(getattr(m, "SORTS", ())), "plugin": n in plugins.SOURCE_INFO,
                         **plugins.SOURCE_INFO.get(n, {})} for n, m in SOURCES.items()]}


@app.post("/api/feed")
async def favorites_feed(body: dict = Body(...)):
    """Recent videos of the given favorite performers / channels (see feed.py)."""
    def names(key):
        raw = body.get(key) if isinstance(body.get(key), list) else []
        return [str(n).strip()[:100] for n in raw[:300] if str(n).strip()]
    return feed.get(names("performers"), names("channels"))


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
