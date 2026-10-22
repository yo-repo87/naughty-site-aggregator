"""Saved-search alerts: a background loop re-runs each saved search every
ALERT_HOURS (default 6) and notifies the chosen target (ntfy, Discord, webhook
or a plug-in) when videos appear that it hasn't reported before. The first run
only records what's there, so turning an alert on doesn't send a flood.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request

from ..searching import fetch_page, supports_new
from ..sources import SOURCES
from ..sources.base import Query
from ..targets import Errors
from . import crypto, db
from .config import settings
from .send import load_target
from .sessions import rate_limit, require_session

router = APIRouter()
log = logging.getLogger("aggregator.alerts")

EVERY = timedelta(hours=float(os.environ.get("ALERT_HOURS") or 6))
MAX_ALERTS = 25
SEEN_MAX = 1500


def _enabled() -> None:
    if not settings.enabled or not db.pool:
        raise HTTPException(404, "Accounts are not enabled on this server.")


def _where(uid, aid, part: str) -> str:
    return f"alerts.{part}:{uid}:{aid}"


def clean_params(p) -> dict:
    if not isinstance(p, dict):
        raise HTTPException(400, "Nothing to save.")
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split(",")
    out = {
        "q": str(p.get("q") or "").strip()[:200],
        "actor": str(p.get("actor") or "").strip()[:100],
        "channel": str(p.get("channel") or "").strip()[:100],
        "tags": [str(t).strip()[:60] for t in tags if str(t).strip()][:10],
        "min_sec": max(0, int(p.get("min_sec") or 0)),
        "max_sec": int(p["max_sec"]) if p.get("max_sec") not in (None, "") else None,
        "sources": [s for s in (p.get("sources") or SOURCES) if s in SOURCES],
    }
    if not (out["q"] or out["actor"] or out["channel"] or out["tags"]):
        raise HTTPException(400, "Alerts need a search: keywords, a performer, a channel or tags.")
    return out


def link(params: dict) -> str:
    q = {k: v for k, v in {
        "q": params["q"], "actor": params["actor"], "channel": params["channel"], "tags": ",".join(params["tags"]),
        "min_sec": params["min_sec"] or "", "max_sec": params["max_sec"] if params["max_sec"] is not None else "",
        "sort": "new",
    }.items() if v not in ("", None)}
    return f"{settings.public_url or ''}/?{urlencode(q)}"


async def _search(params: dict) -> list:
    base = Query(text=params["q"], actor=params["actor"], channel=params["channel"], tags=params["tags"],
                 min_sec=params["min_sec"], max_sec=params["max_sec"])
    names = [n for n in params["sources"] if n in SOURCES]
    results = await asyncio.gather(
        *(fetch_page(n, Query(**{**base.__dict__, "sort": "new" if supports_new(n) else ""})) for n in names),
        return_exceptions=True)
    videos = []
    for res in results:
        if not isinstance(res, BaseException):
            videos.extend(v for v in res if v.duration == 0 or (
                v.duration >= params["min_sec"] - 59 and (params["max_sec"] is None or v.duration <= params["max_sec"])))
    return videos


async def run(row) -> int:
    """Checks one alert; returns how many new videos it found (and notified about)."""
    uid, aid = row["user_id"], row["id"]
    data = crypto.open_json(row["data_enc"], _where(uid, aid, "data"))
    seen = crypto.open_json(row["seen_enc"], _where(uid, aid, "seen")) if row["seen_enc"] else None
    videos = await _search(data["params"])
    keys = [f"{v.source}:{v.id}" for v in videos]
    new = [v for v, k in zip(videos, keys) if seen is not None and k not in set(seen)]
    error = None
    if new and row["target_id"]:
        try:
            m, cfg = await load_target(uid, row["target_id"])
            titles = "\n".join(f"• {v.title}" for v in new[:3]) + (f"\n…and {len(new) - 3} more" if len(new) > 3 else "")
            await m.notify(cfg, f"{len(new)} new video{'s' if len(new) != 1 else ''}: {data['name']}", titles, link(data["params"]))
        except (HTTPException, *Errors) as e:
            error = getattr(e, "detail", None) or str(e)
    merged = keys + [k for k in (seen or []) if k not in set(keys)]
    async with db.pool.acquire() as c:
        await c.execute("UPDATE alerts SET seen_enc = $2, last_run = now(), last_new = $3, last_error = $4 WHERE id = $1",
                        aid, crypto.seal_json(merged[:SEEN_MAX], _where(uid, aid, "seen")),
                        len(new) if seen is not None else 0, str(error)[:300] if error else None)
    return len(new) if seen is not None else 0


async def loop() -> None:
    """Background task: every 15 minutes, run the alerts that are due."""
    while True:
        try:
            if db.pool:
                async with db.pool.acquire() as c:
                    due = await c.fetch("SELECT * FROM alerts WHERE last_run IS NULL OR last_run < $1 ORDER BY last_run NULLS FIRST LIMIT 50",
                                        datetime.now(timezone.utc) - EVERY)
                for row in due:
                    try:
                        await run(row)
                    except Exception as e:
                        log.warning("alert %s failed: %s", row["id"], e)
                    await asyncio.sleep(3)
        except Exception as e:
            log.warning("alert loop: %s", e)
        await asyncio.sleep(900)


async def _mine(uid, aid: UUID):
    async with db.pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM alerts WHERE id = $1 AND user_id = $2", aid, uid)
    if not row:
        raise HTTPException(404, "That alert doesn't exist any more.")
    return row


async def _check_target(uid, target_id) -> UUID | None:
    if not target_id:
        return None
    tid = UUID(str(target_id))
    m, _ = await load_target(uid, tid)
    if not hasattr(m, "notify"):
        raise HTTPException(400, f"{m.LABEL} can't send notifications: pick ntfy, Discord or a webhook.")
    return tid


@router.get("/api/me/alerts")
async def my_alerts(request: Request):
    _enabled()
    s = await require_session(request)
    uid = s["user_id"]
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT * FROM alerts WHERE user_id = $1 ORDER BY created_at", uid)
    out = []
    for r in rows:
        data = crypto.open_json(r["data_enc"], _where(uid, r["id"], "data"))
        out.append({"id": str(r["id"]), "name": data["name"], "params": data["params"],
                    "target_id": str(r["target_id"]) if r["target_id"] else None,
                    "last_run": r["last_run"].isoformat() if r["last_run"] else None,
                    "last_new": r["last_new"], "last_error": r["last_error"]})
    return {"alerts": out, "every_hours": EVERY.total_seconds() / 3600}


@router.post("/api/me/alerts")
async def add_alert(request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    uid = s["user_id"]
    rate_limit(request, "alert-save", 20, 60)
    params = clean_params(body.get("params"))
    name = str(body.get("name") or "").strip()[:80] or "Saved search"
    tid = await _check_target(uid, body.get("target_id"))
    async with db.pool.acquire() as c:
        if await c.fetchval("SELECT count(*) FROM alerts WHERE user_id = $1", uid) >= MAX_ALERTS:
            raise HTTPException(400, f"You can have up to {MAX_ALERTS} alerts.")
        aid = await c.fetchval("SELECT gen_random_uuid()")
        await c.execute("INSERT INTO alerts (id, user_id, target_id, data_enc) VALUES ($1,$2,$3,$4)",
                        aid, uid, tid, crypto.seal_json({"name": name, "params": params}, _where(uid, aid, "data")))
        row = await c.fetchrow("SELECT * FROM alerts WHERE id = $1", aid)
    asyncio.create_task(run(row))  # first run records what's there now
    return {"ok": True, "id": str(aid)}


@router.patch("/api/me/alerts/{aid}")
async def edit_alert(aid: UUID, request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    uid = s["user_id"]
    row = await _mine(uid, aid)
    data = crypto.open_json(row["data_enc"], _where(uid, aid, "data"))
    if "name" in body:
        data["name"] = str(body["name"]).strip()[:80] or data["name"]
    tid = await _check_target(uid, body["target_id"]) if "target_id" in body else row["target_id"]
    async with db.pool.acquire() as c:
        await c.execute("UPDATE alerts SET data_enc = $2, target_id = $3 WHERE id = $1",
                        aid, crypto.seal_json(data, _where(uid, aid, "data")), tid)
    return {"ok": True}


@router.delete("/api/me/alerts/{aid}")
async def delete_alert(aid: UUID, request: Request):
    _enabled()
    s = await require_session(request)
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM alerts WHERE id = $1 AND user_id = $2", aid, s["user_id"])
    return {"ok": True}


@router.post("/api/me/alerts/{aid}/run")
async def run_now(aid: UUID, request: Request):
    _enabled()
    s = await require_session(request)
    rate_limit(request, "alert-run", 6, 60)
    n = await run(await _mine(s["user_id"], aid))
    return {"ok": True, "new": n}


async def count() -> int:
    async with db.pool.acquire() as c:
        return await c.fetchval("SELECT count(*) FROM alerts")
