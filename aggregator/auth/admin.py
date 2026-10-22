"""Admin page: accounts, the registration switch, site health, plug-ins and the
performer/channel directory. Admins are the accounts whose email is listed in
ADMIN_EMAILS; every endpoint re-checks that on each request.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request, Response

from .. import directory, feed, health, plugins
from ..sources import SOURCES
from . import crypto, db
from .config import settings
from .sessions import log_event, now, require_session, require_step_up

router = APIRouter()


def is_admin(u) -> bool:
    if not settings.admin_emails or not u or not u["email_enc"]:
        return False
    return crypto.open_(u["email_enc"], f"users.email:{u['id']}") in settings.admin_emails


async def _admin(request: Request, step_up: bool = False):
    if not settings.enabled or not db.pool:
        raise HTTPException(404, "Accounts are not enabled on this server.")
    s = await (require_step_up(request) if step_up else require_session(request))
    async with db.pool.acquire() as c:
        u = await c.fetchrow("SELECT * FROM users WHERE id = $1", s["user_id"])
    if not is_admin(u):
        raise HTTPException(403, "Admins only.")
    return s


async def load_app_settings() -> None:
    """Runtime overrides saved from the admin page win over the environment."""
    async with db.pool.acquire() as c:
        v = await c.fetchval("SELECT value FROM app_settings WHERE key = 'allow_registration'")
    if v is not None:
        settings.allow_registration = v == "true"


@router.get("/api/admin/overview")
async def overview(request: Request):
    s = await _admin(request)
    async with db.pool.acquire() as c:
        rows = await c.fetch("""
            SELECT u.*,
              (SELECT count(*) FROM webauthn_credentials w WHERE w.user_id = u.id AND w.kind = 'passkey') AS passkeys,
              (SELECT count(*) FROM webauthn_credentials w WHERE w.user_id = u.id AND w.kind = 'security_key') AS keys,
              (SELECT count(*) FROM sessions x WHERE x.user_id = u.id AND x.expires_at > now()) AS sessions,
              (SELECT count(*) FROM user_targets t WHERE t.user_id = u.id) AS targets,
              (SELECT count(*) FROM alerts a WHERE a.user_id = u.id) AS alerts
            FROM users u ORDER BY u.created_at""")
        alert_count = await c.fetchval("SELECT count(*) FROM alerts")
    users = [{
        "id": str(r["id"]),
        "name": crypto.open_(r["name_enc"], f"users.name:{r['id']}"),
        "email": crypto.open_(r["email_enc"], f"users.email:{r['id']}") if r["email_enc"] else None,
        "created_at": r["created_at"].isoformat(),
        "last_login_at": r["last_login_at"].isoformat() if r["last_login_at"] else None,
        "two_step": r["totp_enabled"] or r["keys"] > 0, "passkeys": r["passkeys"], "sessions": r["sessions"],
        "targets": r["targets"], "alerts": r["alerts"], "admin": is_admin(r), "you": r["id"] == s["user_id"],
        "locked": bool(r["locked_until"]) and r["locked_until"] > now(),
    } for r in rows]
    return {
        "users": users,
        "registration": settings.allow_registration,
        "require_login": settings.require_login,
        "sites": health.report(SOURCES),
        "plugins": plugins.loaded,
        "plugins_dir": str(plugins.PLUGINS_DIR),
        "directory": {**directory.status, "performers": len(directory.performers.entries),
                      "channels": len(directory.channels.entries), "built": directory.performers.built},
        "feed": feed.stats(),
        "alerts": alert_count,
    }


@router.post("/api/admin/registration")
async def set_registration(request: Request, body: dict = Body(...)):
    s = await _admin(request)
    on = bool(body.get("open"))
    async with db.pool.acquire() as c:
        await c.execute("""INSERT INTO app_settings (key, value) VALUES ('allow_registration', $1)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", "true" if on else "false")
    settings.allow_registration = on
    await log_event(s["user_id"], "admin_registration_" + ("opened" if on else "closed"), request)
    return {"ok": True, "registration": on}


@router.post("/api/admin/users/{uid}/signout")
async def sign_out_user(uid: UUID, request: Request):
    s = await _admin(request)
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM sessions WHERE user_id = $1 AND token_hash <> $2", uid, s["token_hash"])
    return {"ok": True}


@router.delete("/api/admin/users/{uid}")
async def delete_user(uid: UUID, request: Request, response: Response):
    s = await _admin(request, step_up=True)
    if uid == s["user_id"]:
        raise HTTPException(400, "Delete your own account from the Profile tab.")
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM users WHERE id = $1", uid)  # cascades to everything
    return {"ok": True}


@router.post("/api/admin/directory/rebuild")
async def rebuild_directory(request: Request):
    await _admin(request)
    if not directory.rebuild_now():
        raise HTTPException(409, "A rebuild is already running.")
    return {"ok": True}
