"""Each account's 'send to' targets (Profile tab) and sending videos to them.

Configs are sealed per row (AES-GCM, bound to user + row id). Secret fields
(type "password") never go back to the browser; on edit, leaving one blank keeps
the saved value only while every address field still points at the same origin,
so someone at an unlocked screen can't re-point a saved password elsewhere.
"""
from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request

from .. import net
from ..sources import SITE_DOMAINS
from ..targets import TARGETS, Errors, describe, secret_fields, url_fields
from . import crypto, db
from .config import settings
from .sessions import rate_limit, require_session

router = APIRouter()
MAX_TARGETS = 10


def _enabled() -> None:
    if not settings.enabled or not db.pool:
        raise HTTPException(404, "Accounts are not enabled on this server.")


def _where(uid, tid) -> str:
    return f"user_targets.config:{uid}:{tid}"


def _name_where(uid, tid) -> str:
    return f"user_targets.name:{uid}:{tid}"


async def list_targets(uid) -> list[dict]:
    """[{id, kind, name, config (secrets removed), secrets: {field: bool}}]"""
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT * FROM user_targets WHERE user_id = $1 ORDER BY created_at", uid)
    out = []
    for r in rows:
        m = TARGETS.get(r["kind"])
        cfg = crypto.open_json(r["config_enc"], _where(uid, r["id"])) or {}
        secrets = secret_fields(m) if m else set()
        out.append({
            "id": str(r["id"]), "kind": r["kind"], "label": m.LABEL if m else r["kind"],
            "name": crypto.open_(r["name_enc"], _name_where(uid, r["id"])) or (m.LABEL if m else r["kind"]),
            "config": {k: v for k, v in cfg.items() if k not in secrets},
            "secrets": {k: bool(cfg.get(k)) for k in secrets},
            "available": m is not None,  # a plug-in that's gone
            "can_send": bool(m and hasattr(m, "send")), "can_notify": bool(m and hasattr(m, "notify")),
            "has_status": bool(m and hasattr(m, "status")),
        })
    return out


async def load_target(uid, tid: UUID):
    async with db.pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM user_targets WHERE id = $1 AND user_id = $2", tid, uid)
    if not r:
        raise HTTPException(404, "That destination doesn't exist any more.")
    m = TARGETS.get(r["kind"])
    if not m:
        raise HTTPException(400, f"The {r['kind']} plug-in isn't installed on this server any more.")
    return m, crypto.open_json(r["config_enc"], _where(uid, r["id"])) or {}


def _clean_config(m, raw: dict, old: dict | None) -> dict:
    cfg = {}
    for f in getattr(m, "FIELDS", []):
        v = raw.get(f["name"])
        v = "" if v is None else str(v)
        v = v if f.get("type") == "password" else v.strip()
        if len(v) > 1000:
            raise HTTPException(400, f"{f['label']} is too long.")
        cfg[f["name"]] = v
    if old:
        same_place = all(net.origin(old.get(k) or "") == net.origin(cfg.get(k) or "") for k in url_fields(m))
        for k in secret_fields(m):
            if not cfg.get(k) and old.get(k) and same_place:
                cfg[k] = old[k]
    for f in getattr(m, "FIELDS", []):
        if f.get("required") and not cfg.get(f["name"]):
            raise HTTPException(400, f"Fill in {f['label']}.")
    return cfg


async def _check(m, cfg: dict) -> str:
    try:
        return await m.check(cfg)
    except Errors as e:
        raise HTTPException(400, str(e)) from None


@router.get("/api/targets/kinds")
async def kinds(request: Request):
    _enabled()
    await require_session(request)
    return {"kinds": [describe(m) for m in TARGETS.values()]}


@router.get("/api/me/targets")
async def my_targets(request: Request):
    _enabled()
    s = await require_session(request)
    return {"targets": await list_targets(s["user_id"])}


@router.post("/api/me/targets")
async def add_target(request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    uid = s["user_id"]
    rate_limit(request, "target-save", 10, 60)
    m = TARGETS.get(str(body.get("kind")))
    if not m:
        raise HTTPException(400, "Pick what to connect.")
    async with db.pool.acquire() as c:
        if await c.fetchval("SELECT count(*) FROM user_targets WHERE user_id = $1", uid) >= MAX_TARGETS:
            raise HTTPException(400, f"You can connect up to {MAX_TARGETS} destinations.")
    cfg = _clean_config(m, body.get("config") or {}, None)
    status = await _check(m, cfg)
    name = str(body.get("name") or "").strip()[:60] or m.LABEL
    async with db.pool.acquire() as c:
        tid = await c.fetchval("SELECT gen_random_uuid()")
        await c.execute("INSERT INTO user_targets (id, user_id, kind, name_enc, config_enc) VALUES ($1,$2,$3,$4,$5)",
                        tid, uid, m.KIND, crypto.seal(name, _name_where(uid, tid)), crypto.seal_json(cfg, _where(uid, tid)))
    return {"ok": True, "id": str(tid), "status": status}


@router.put("/api/me/targets/{tid}")
async def edit_target(tid: UUID, request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    uid = s["user_id"]
    rate_limit(request, "target-save", 10, 60)
    m, old = await load_target(uid, tid)
    cfg = _clean_config(m, body.get("config") or {}, old)
    status = await _check(m, cfg)
    name = str(body.get("name") or "").strip()[:60] or m.LABEL
    async with db.pool.acquire() as c:
        await c.execute("UPDATE user_targets SET name_enc = $3, config_enc = $4 WHERE id = $1 AND user_id = $2",
                        tid, uid, crypto.seal(name, _name_where(uid, tid)), crypto.seal_json(cfg, _where(uid, tid)))
    return {"ok": True, "status": status}


@router.delete("/api/me/targets/{tid}")
async def delete_target(tid: UUID, request: Request):
    _enabled()
    s = await require_session(request)
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM user_targets WHERE id = $1 AND user_id = $2", tid, s["user_id"])
    return {"ok": True}


def _video(raw) -> dict:
    if not isinstance(raw, dict):
        raise HTTPException(400, "No video given.")
    url = str(raw.get("url") or "").strip()
    u = urlsplit(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not any(host == d or host.endswith("." + d) for d in SITE_DOMAINS) or len(url) > 1000:
        raise HTTPException(400, "Only videos from the supported sites can be sent.")
    thumb = str(raw.get("thumb") or "")
    duration = raw.get("duration")
    return {
        "url": url, "title": str(raw.get("title") or "Video").strip()[:300] or "Video",
        "source": str(raw.get("source") or "")[:30],
        "duration": int(duration) if isinstance(duration, (int, float)) and 0 <= duration < 10**6 else None,
        "thumb": thumb[:1000] if thumb.startswith("https://") else "",
    }


@router.post("/api/me/targets/{tid}/send")
async def send(tid: UUID, request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    rate_limit(request, "target-send", 60, 60)
    m, cfg = await load_target(s["user_id"], tid)
    if not hasattr(m, "send"):
        raise HTTPException(400, f"{m.LABEL} can't receive videos.")
    video = _video(body.get("video"))
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    if isinstance(getattr(m, "OPTIONS", None), list):  # plug-in style options: only listed choices pass
        allowed = {o["name"]: {c[0] for c in o.get("choices", [])} for o in m.OPTIONS}
        options = {k: str(v) for k, v in options.items() if k in allowed and str(v) in allowed[k]}
    try:
        message = await m.send(cfg, video, options)
    except Errors as e:
        raise HTTPException(502, str(e)) from None
    from . import downloads  # (imported here: downloads imports this module)
    try:
        await downloads.on_sent(s["user_id"], tid, video)
    except Exception:  # an update that can't be queued must not fail the send itself
        pass
    return {"ok": True, "message": message}


@router.post("/api/me/targets/{tid}/status")
async def status(tid: UUID, request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    rate_limit(request, "target-status", 30, 60)
    m, cfg = await load_target(s["user_id"], tid)
    urls = [str(u) for u in (body.get("urls") or [])[:200] if isinstance(u, str)]
    if not hasattr(m, "status") or not urls:
        return {"status": {}}
    try:
        return {"status": await m.status(cfg, urls)}
    except Errors as e:
        raise HTTPException(502, str(e)) from None


@router.post("/api/me/targets/{tid}/test")
async def test_notify(tid: UUID, request: Request):
    _enabled()
    s = await require_session(request)
    rate_limit(request, "target-test", 5, 60)
    m, cfg = await load_target(s["user_id"], tid)
    if not hasattr(m, "notify"):
        raise HTTPException(400, f"{m.LABEL} can't send notifications.")
    try:
        await m.notify(cfg, "Aggregator test", "Saved-search alerts will look like this.", settings.public_url or "")
    except Errors as e:
        raise HTTPException(502, str(e)) from None
    return {"ok": True}


async def migrate_metube() -> None:
    """One-off: the first MeTube version kept one config in users.metube_enc."""
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT id, metube_enc FROM users WHERE metube_enc IS NOT NULL")
        for r in rows:
            cfg = crypto.open_json(r["metube_enc"], f"users.metube:{r['id']}")
            async with c.transaction():
                tid = await c.fetchval("SELECT gen_random_uuid()")
                await c.execute("INSERT INTO user_targets (id, user_id, kind, name_enc, config_enc) VALUES ($1,$2,'metube',$3,$4)",
                                tid, r["id"], crypto.seal("MeTube", _name_where(r["id"], tid)),
                                crypto.seal_json({"url": cfg["url"], "username": cfg.get("username") or "",
                                                  "password": cfg.get("password") or ""}, _where(r["id"], tid)))
                await c.execute("UPDATE users SET metube_enc = NULL WHERE id = $1", r["id"])
