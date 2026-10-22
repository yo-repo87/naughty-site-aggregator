"""'New from your favorites': recent videos of favorite performers and channels.

Results are kept per name and shared by every user who favorited that name, so
300 users following the same star cost one lookup. A name is looked up (newest
first, on the sites that can sort that way) when someone asks for it and the
copy is older than FEED_REFRESH_HOURS; in between, a background loop refreshes
names someone asked for within the last two weeks. Lookups go one name at a
time with a pause, because Pornhub throttles bursts.

Each video carries first_seen: when this server first saw it for that name.
Videos from a name's very first lookup get 0, so newly favorited names don't
flood the "new" badge; the browser compares first_seen with the time the user
last opened the feed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
import unicodedata

from .searching import fetch_page, supports_new
from .sources import SOURCES
from .sources.base import Query

log = logging.getLogger("aggregator.feed")

REFRESH = float(os.environ.get("FEED_REFRESH_HOURS") or 6) * 3600
KEEP_REQUESTED = 14 * 86400   # refresh in the background while someone asked within this
FORGET = 30 * 86400           # drop names nobody asked for in this long
PER_NAME = 40
PAUSE = 2.0                   # seconds between names
CACHE_FILE = os.path.join(os.environ.get("DIRECTORY_DIR") or tempfile.gettempdir(), "aggregator-feed-v1.json")

_entries: dict[str, dict] = {}   # "performer:rileyreid" -> {kind, name, at, requested, videos, error}
_queue: asyncio.Queue | None = None
_queued: set[str] = set()
_dirty = 0


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def key(kind: str, name: str) -> str:
    return f"{kind}:{norm(name)}"


def _matches(kind: str, name: str, v) -> bool:
    """Sites without a performer/channel filter return keyword matches: keep the real ones."""
    k = norm(name)
    if kind == "performer":
        return any(norm(a) == k for a in v.actors) or k in norm(v.title)
    return (norm(v.uploader) == k) if v.uploader else k in norm(v.title)


async def _lookup(entry: dict) -> None:
    kind, name = entry["kind"], entry["name"]
    q = Query(actor=name, sort="new") if kind == "performer" else Query(channel=name, sort="new")
    names = [n for n in SOURCES if supports_new(n)]
    results = await asyncio.gather(*(fetch_page(n, q) for n in names), return_exceptions=True)
    fresh, errors = [], []
    for n, res in zip(names, results):
        if isinstance(res, BaseException):
            errors.append(n)
            continue
        fresh.extend(v for v in res[:20] if _matches(kind, name, v))
    known = {f"{v['source']}:{v['id']}": v.get("first_seen", 0) for v in entry["videos"]}
    first_lookup = not entry["at"]
    now = time.time()
    out = []
    for v in fresh:
        d = v.to_dict()
        d["first_seen"] = known.get(f"{v.source}:{v.id}", 0 if first_lookup else now)
        out.append(d)
    # Keep older videos that dropped off the sites' first page, up to PER_NAME.
    ids = {f"{d['source']}:{d['id']}" for d in out}
    out += [v for v in entry["videos"] if f"{v['source']}:{v['id']}" not in ids]
    out.sort(key=lambda d: (d["first_seen"], d.get("published") or ""), reverse=True)
    entry["videos"] = out[:PER_NAME]
    entry["at"] = now
    entry["error"] = f"{len(errors)} site(s) failed" if errors else ""


async def worker() -> None:
    """Background task: does the lookups one at a time, and re-queues stale names."""
    global _queue, _dirty
    _queue = asyncio.Queue()
    _load()
    last_sweep = 0.0
    while True:
        if time.time() - last_sweep > 600:
            last_sweep = time.time()
            _sweep()
        try:
            k = await asyncio.wait_for(_queue.get(), timeout=600)
        except asyncio.TimeoutError:
            continue
        _queued.discard(k)
        entry = _entries.get(k)
        if not entry or time.time() - entry["at"] < REFRESH:
            continue
        try:
            await _lookup(entry)
        except Exception as e:
            log.warning("feed lookup %s failed: %s", k, e)
            entry["at"] = entry["at"] or 1  # don't retry immediately
        _dirty += 1
        if _dirty >= 10 or _queue.empty():
            _save()
        await asyncio.sleep(PAUSE)


def _enqueue(k: str) -> None:
    if _queue is not None and k not in _queued:
        _queued.add(k)
        _queue.put_nowait(k)


def _sweep() -> None:
    now = time.time()
    for k, e in list(_entries.items()):
        if now - e["requested"] > FORGET:
            del _entries[k]
        elif now - e["requested"] < KEEP_REQUESTED and now - e["at"] >= REFRESH:
            _enqueue(k)


def get(performers: list[str], channels: list[str]) -> dict:
    now = time.time()
    items, pending, updated = [], 0, 0.0
    for kind, names in (("performer", performers), ("channel", channels)):
        for name in names:
            k = key(kind, name)
            if not k.split(":", 1)[1]:
                continue
            entry = _entries.get(k)
            if entry is None:
                entry = _entries[k] = {"kind": kind, "name": name, "at": 0.0, "requested": now, "videos": [], "error": ""}
            entry["requested"] = now
            if now - entry["at"] >= REFRESH:
                _enqueue(k)
            if not entry["at"]:
                pending += 1
                continue
            updated = max(updated, entry["at"])
            for v in entry["videos"][:20]:
                items.append({**v, "feed": {"kind": kind, "name": name}})
    return {"items": items, "pending": pending, "updated": updated, "refresh_hours": REFRESH / 3600}


def stats() -> dict:
    return {"names": len(_entries), "queued": len(_queued),
            "checked_24h": sum(1 for e in _entries.values() if time.time() - e["at"] < 86400)}


def _load() -> None:
    try:
        with open(CACHE_FILE) as f:
            _entries.update(json.load(f))
    except (OSError, ValueError):
        pass


def _save() -> None:
    global _dirty
    _dirty = 0
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_entries, f)
        os.replace(tmp, CACHE_FILE)
    except OSError as e:
        log.warning("feed cache not written: %s", e)
