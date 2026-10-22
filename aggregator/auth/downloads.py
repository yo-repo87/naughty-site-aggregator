"""Download updates: a notification (Gotify, ntfy, Discord, Telegram... any target
with notify()) when a video sent with the Download button starts, and when it
finishes or fails.

- Started: right after the destination accepted the video. Sends within a few
  seconds of each other (select mode) are combined into one message.
- Finished / failed: for destinations that report progress (MeTube), the server
  checks every minute, also when no page is open, for up to two days.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request

from ..targets import TARGETS, Errors
from . import crypto, db
from .config import settings
from .send import load_target
from .sessions import now, require_session

router = APIRouter()
log = logging.getLogger("aggregator.downloads")

EVENTS = {"start", "finish"}
GIVE_UP = timedelta(days=2)
COALESCE = 6  # seconds: sends this close together share one "started" message
_pending: dict = {}  # user id -> {"items": [(title, url, target name)], "task": Task}


def _where(uid, wid) -> str:
    return f"download_watches.data:{uid}:{wid}"


async def _prefs(uid):
    async with db.pool.acquire() as c:
        r = await c.fetchrow("SELECT dl_notify_target, dl_notify_events FROM users WHERE id = $1", uid)
    events = set((r["dl_notify_events"] or "").split(",")) & EVENTS if r else set()
    return (r["dl_notify_target"] if r else None), events


async def _target_name(uid, tid) -> str:
    async with db.pool.acquire() as c:
        enc = await c.fetchval("SELECT name_enc FROM user_targets WHERE id = $1 AND user_id = $2", tid, uid)
    return crypto.open_(enc, f"user_targets.name:{uid}:{tid}") or "your downloader"


async def _notify(uid, title: str, message: str, link: str) -> None:
    tid, _ = await _prefs(uid)
    if not tid:
        return
    try:
        m, cfg = await load_target(uid, tid)
        await m.notify(cfg, title, message, link)
    except (HTTPException, *Errors) as e:
        log.warning("download update for %s not delivered: %s", uid, getattr(e, "detail", e))


def _lines(items, limit=5) -> str:
    out = "\n".join(f"• {t}" for t, *_ in items[:limit])
    return out + (f"\n…and {len(items) - limit} more" if len(items) > limit else "")


async def _flush_started(uid) -> None:
    await asyncio.sleep(COALESCE)
    items = _pending.pop(uid, {}).get("items", [])
    if not items:
        return
    where = items[0][2]
    if len(items) == 1:
        await _notify(uid, f"Download started on {where}", items[0][0], items[0][1])
    else:
        await _notify(uid, f"{len(items)} downloads started on {where}", _lines(items), "")


async def on_sent(uid, target_id: UUID, video: dict) -> None:
    """Called after a destination accepted a video."""
    tid, events = await _prefs(uid)
    if not tid or tid == target_id:  # don't announce a send to the notifier itself
        return
    name = await _target_name(uid, target_id)
    if "start" in events:
        entry = _pending.setdefault(uid, {"items": []})
        entry["items"].append((video["title"], video["url"], name))
        if "task" not in entry:
            entry["task"] = asyncio.create_task(_flush_started(uid))
    async with db.pool.acquire() as c:
        kind = await c.fetchval("SELECT kind FROM user_targets WHERE id = $1", target_id)
    if "finish" in events and hasattr(TARGETS.get(kind), "status"):
        async with db.pool.acquire() as c:
            wid = await c.fetchval("SELECT gen_random_uuid()")
            await c.execute("INSERT INTO download_watches (id, user_id, target_id, data_enc) VALUES ($1,$2,$3,$4)",
                            wid, uid, target_id, crypto.seal_json({"url": video["url"], "title": video["title"]}, _where(uid, wid)))


async def _check_all() -> None:
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM download_watches WHERE created_at < $1", now() - GIVE_UP)
        rows = await c.fetch("SELECT * FROM download_watches ORDER BY created_at")
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["user_id"], r["target_id"]), []).append(r)
    for (uid, tid), watches in groups.items():
        _, events = await _prefs(uid)
        if "finish" not in events:  # turned off since: stop watching
            async with db.pool.acquire() as c:
                await c.execute("DELETE FROM download_watches WHERE user_id = $1", uid)
            continue
        try:
            m, cfg = await load_target(uid, tid)
            items = {w["id"]: crypto.open_json(w["data_enc"], _where(uid, w["id"])) for w in watches}
            states = await m.status(cfg, [d["url"] for d in items.values()])
        except (HTTPException, *Errors):
            continue  # MeTube unreachable: try again next minute
        done, failed = [], []
        created = {w["id"]: w["created_at"].timestamp() for w in watches}
        for wid, d in items.items():
            info = states.get(d["url"], {})
            if info.get("added") and info["added"] < created[wid] - 300:
                continue  # an older download of the same video, not this one
            st = info.get("status")
            if st == "finished":
                done.append((d["title"], d["url"], wid))
            elif st == "error":
                failed.append((d["title"], d["url"], wid, states[d["url"]].get("msg") or ""))
        if not done and not failed:
            continue
        name = await _target_name(uid, tid)
        link = cfg.get("url") or ""  # MeTube's own page, where the files are
        if len(done) == 1:
            await _notify(uid, f"Download finished on {name}", done[0][0], link)
        elif done:
            await _notify(uid, f"{len(done)} downloads finished on {name}", _lines(done), link)
        for title, url, _, msg in failed:
            await _notify(uid, f"Download failed on {name}", f"{title}\n{msg}".strip()[:500], link or url)
        async with db.pool.acquire() as c:
            await c.execute("DELETE FROM download_watches WHERE id = ANY($1::uuid[])", [x[2] for x in done + failed])


async def loop() -> None:
    """Background task: checks watched downloads every minute."""
    while True:
        await asyncio.sleep(60)
        try:
            if db.pool:
                await _check_all()
        except Exception as e:
            log.warning("download updates: %s", e)


@router.get("/api/me/download-updates")
async def get_updates(request: Request):
    if not settings.enabled or not db.pool:
        raise HTTPException(404, "Accounts are not enabled on this server.")
    s = await require_session(request)
    tid, events = await _prefs(s["user_id"])
    async with db.pool.acquire() as c:
        watching = await c.fetchval("SELECT count(*) FROM download_watches WHERE user_id = $1", s["user_id"])
    return {"target_id": str(tid) if tid else None, "events": sorted(events), "watching": watching}


@router.put("/api/me/download-updates")
async def put_updates(request: Request, body: dict = Body(...)):
    if not settings.enabled or not db.pool:
        raise HTTPException(404, "Accounts are not enabled on this server.")
    s = await require_session(request)
    uid = s["user_id"]
    tid = body.get("target_id") or None
    events = [e for e in (body.get("events") or []) if e in EVENTS]
    if tid:
        tid = UUID(str(tid))
        m, _ = await load_target(uid, tid)
        if not hasattr(m, "notify"):
            raise HTTPException(400, f"{m.LABEL} can't send notifications.")
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET dl_notify_target = $2, dl_notify_events = $3 WHERE id = $1",
                        uid, tid, ",".join(sorted(events)) if tid else None)
        if not tid or "finish" not in events:
            await c.execute("DELETE FROM download_watches WHERE user_id = $1", uid)
    return {"ok": True}
