"""Account endpoints: /api/auth/* (signing in) and /api/me/* (your own account)."""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
from datetime import timedelta
from uuid import UUID

import pyotp
import segno
from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from urllib.parse import quote

from . import challenges, crypto, db, oauth, passkeys
from .config import settings
from .sessions import (
    STEP_UP_MINUTES, create_session, current_session, end_session, finish_pending, log_event, now,
    pending_user, rate_limit, require_session, require_step_up, start_pending,
)

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,63}$")
MIN_PASSWORD = 10
LOCK_AFTER = 8
LOCK_MINUTES = 15
BACKUP_CODES = 10
SETTINGS_MAX = 256 * 1024  # filters + up to 300 favorite performers with picture URLs
EVENT_LABELS = {
    "registered": "Account created", "signed_in": "Signed in", "signed_out": "Signed out",
    "login_failed": "Failed sign-in attempt", "password_set": "Password changed",
    "totp_enabled": "Authenticator app turned on", "totp_disabled": "Authenticator app turned off",
    "backup_codes": "New backup codes created", "backup_code_used": "Backup code used",
    "passkey_added": "Passkey added", "security_key_added": "Security key added",
    "credential_removed": "Passkey or security key removed", "identity_linked": "Account connected",
    "identity_unlinked": "Account disconnected", "sessions_revoked": "Signed out other devices",
    "email_changed": "Email changed", "reauth": "Confirmed identity",
}


def _enabled() -> None:
    if not settings.enabled or not db.pool:
        raise HTTPException(404, "Accounts are not enabled on this server.")


async def _user(user_id: UUID):
    async with db.pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE id = $1", user_id)


def _email(u) -> str | None:
    return crypto.open_(u["email_enc"], f"users.email:{u['id']}")


def _name(u) -> str | None:
    return crypto.open_(u["name_enc"], f"users.name:{u['id']}")


async def _needs_second_step(u) -> list[str]:
    async with db.pool.acquire() as c:
        keys = await c.fetchval("SELECT count(*) FROM webauthn_credentials WHERE user_id = $1", u["id"])
        has_sec_key = await c.fetchval(
            "SELECT count(*) FROM webauthn_credentials WHERE user_id = $1 AND kind = 'security_key'", u["id"])
        codes = await c.fetchval("SELECT count(*) FROM backup_codes WHERE user_id = $1 AND used_at IS NULL", u["id"])
    if not (u["totp_enabled"] or has_sec_key):
        return []
    methods = []
    if u["totp_enabled"]:
        methods.append("totp")
    if keys:
        methods.append("key")
    if codes:
        methods.append("backup")
    return methods


async def _sign_in(request: Request, response: Response, u) -> dict:
    """Password/OAuth accepted: either finish, or ask for the second step."""
    methods = await _needs_second_step(u)
    if methods:
        await start_pending(request, response, u["id"])
        return {"second_step": methods}
    await create_session(request, response, u["id"])
    return {"ok": True}


def _check_password(pw: str, email: str | None = None) -> None:
    if not (MIN_PASSWORD <= len(pw) <= 256):
        raise HTTPException(400, f"Use a password of at least {MIN_PASSWORD} characters.")
    if email and pw.strip().lower() == email:
        raise HTTPException(400, "Your password can't be your email.")


def _new_backup_codes() -> list[str]:
    return [f"{c[:5]}-{c[5:]}" for c in (secrets.token_hex(5) for _ in range(BACKUP_CODES))]


def _code_hash(user_id: UUID, code: str) -> bytes:
    return crypto.index("backup", f"{user_id}:{re.sub(r'[^0-9a-z]', '', code.lower())}")


async def _store_backup_codes(user_id: UUID) -> list[str]:
    codes = _new_backup_codes()
    async with db.pool.acquire() as c, c.transaction():
        await c.execute("DELETE FROM backup_codes WHERE user_id = $1", user_id)
        await c.executemany("INSERT INTO backup_codes (user_id, code_hash) VALUES ($1, $2)",
                            [(user_id, _code_hash(user_id, x)) for x in codes])
    return codes


async def _check_totp(u, code: str) -> bool:
    """Accepts the current code (±30 s) once; a code can't be replayed."""
    code = re.sub(r"\s", "", code or "")
    secret = crypto.open_(u["totp_secret_enc"], f"users.totp:{u['id']}")
    if not (secret and re.fullmatch(r"\d{6}", code)):
        return False
    totp = pyotp.TOTP(secret)
    step_now = int(now().timestamp()) // 30
    for step in (step_now - 1, step_now, step_now + 1):
        if hmac.compare_digest(totp.generate_otp(step), code) and step > (u["totp_last_step"] or 0):
            async with db.pool.acquire() as c:
                await c.execute("UPDATE users SET totp_last_step = $2 WHERE id = $1", u["id"], step)
            return True
    return False


async def _use_backup_code(u, code: str) -> bool:
    async with db.pool.acquire() as c:
        used = await c.fetchval(
            "UPDATE backup_codes SET used_at = now() WHERE user_id = $1 AND code_hash = $2 AND used_at IS NULL RETURNING 1",
            u["id"], _code_hash(u["id"], code))
    return bool(used)


async def _sign_in_methods(user_id: UUID) -> int:
    async with db.pool.acquire() as c:
        return await c.fetchval("""
            SELECT (CASE WHEN password_hash IS NOT NULL THEN 1 ELSE 0 END)
                 + (SELECT count(*) FROM webauthn_credentials WHERE user_id = $1)
                 + (SELECT count(*) FROM oauth_identities WHERE user_id = $1)
            FROM users WHERE id = $1""", user_id)


async def _mark_verified(request: Request) -> None:
    s = await current_session(request)
    if s:
        async with db.pool.acquire() as c:
            await c.execute("UPDATE sessions SET verified_at = now() WHERE token_hash = $1", s["token_hash"])


# ======================= public: configuration =======================

@router.get("/api/auth/config")
async def auth_config():
    if not settings.enabled:
        return {"enabled": False}
    return {
        "enabled": True,
        "registration": settings.allow_registration,
        "require_login": settings.require_login,
        "providers": [{"name": p.name, "label": p.label} for p in settings.providers.values()],
    }


# ======================= public: password sign-in / register =======================

@router.post("/api/auth/register")
async def register(request: Request, response: Response, body: dict = Body(...)):
    _enabled()
    rate_limit(request, "register", 5, 600)
    if not settings.allow_registration:
        raise HTTPException(403, "New accounts are switched off on this server.")
    email = crypto.norm_email(str(body.get("email", "")))
    password = str(body.get("password", ""))
    name = str(body.get("name", "")).strip()[:80] or email.split("@")[0]
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    _check_password(password, email)
    pw_hash = crypto.hash_password(password)
    async with db.pool.acquire() as c:
        if await c.fetchval("SELECT 1 FROM users WHERE email_idx = $1", crypto.index("email", email)):
            raise HTTPException(409, "An account with this email already exists. Sign in instead.")
        uid = await c.fetchval("INSERT INTO users (webauthn_handle) VALUES ($1) RETURNING id", os.urandom(32))
        await c.execute("UPDATE users SET email_idx=$2, email_enc=$3, name_enc=$4, password_hash=$5 WHERE id=$1",
                        uid, crypto.index("email", email), crypto.seal(email, f"users.email:{uid}"),
                        crypto.seal(name, f"users.name:{uid}"), pw_hash)
    await log_event(uid, "registered", request)
    await create_session(request, response, uid)
    return {"ok": True}


@router.post("/api/auth/login")
async def login(request: Request, response: Response, body: dict = Body(...)):
    _enabled()
    rate_limit(request, "login", 10, 60)
    email = crypto.norm_email(str(body.get("email", "")))
    password = str(body.get("password", ""))
    async with db.pool.acquire() as c:
        u = await c.fetchrow("SELECT * FROM users WHERE email_idx = $1", crypto.index("email", email))
    ok, rehash = crypto.verify_password(u["password_hash"] if u else None, password)
    if u and u["locked_until"] and u["locked_until"] > now():
        raise HTTPException(429, "Too many failed attempts on this account. Try again in a few minutes.")
    if not (u and ok):
        if u:
            async with db.pool.acquire() as c:
                fails = await c.fetchval("UPDATE users SET failed_logins = failed_logins + 1 WHERE id = $1 RETURNING failed_logins", u["id"])
                if fails >= LOCK_AFTER:
                    await c.execute("UPDATE users SET locked_until = $2, failed_logins = 0 WHERE id = $1",
                                    u["id"], now() + timedelta(minutes=LOCK_MINUTES))
            await log_event(u["id"], "login_failed", request)
        raise HTTPException(401, "Email or password is incorrect.")
    if rehash:
        async with db.pool.acquire() as c:
            await c.execute("UPDATE users SET password_hash = $2 WHERE id = $1", u["id"], rehash)
    return await _sign_in(request, response, u)


# ======================= public: second step =======================

@router.post("/api/auth/second/code")
async def second_code(request: Request, response: Response, body: dict = Body(...)):
    _enabled()
    rate_limit(request, "second", 10, 60)
    uid = await pending_user(request)
    u = await _user(uid)
    code = str(body.get("code", ""))
    if await _check_totp(u, code):
        pass
    elif await _use_backup_code(u, code):
        await log_event(uid, "backup_code_used", request)
    else:
        raise HTTPException(401, "That code didn't work. Check it and try again.")
    await finish_pending(request)
    await create_session(request, response, uid)
    return {"ok": True}


@router.post("/api/auth/second/key/options")
async def second_key_options(request: Request, response: Response):
    _enabled()
    uid = await pending_user(request)
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT credential_id, transports FROM webauthn_credentials WHERE user_id = $1", uid)
    opts, chal = await passkeys.authentication_options(request, rows, require_uv=False)
    await challenges.put(request, response, "wa_second", {"challenge": chal}, uid)
    return opts


@router.post("/api/auth/second/key/verify")
async def second_key_verify(request: Request, response: Response, body: dict = Body(...)):
    _enabled()
    rate_limit(request, "second", 10, 60)
    data, uid = await challenges.take(request, response, "wa_second")
    if uid != await pending_user(request):
        raise HTTPException(400, "This request expired. Please try again.")
    await passkeys.verify_authentication(request, body.get("credential") or {}, data["challenge"], False, uid)
    await finish_pending(request)
    await create_session(request, response, uid)
    return {"ok": True}


# ======================= public: passkey / security key sign-in =======================

@router.post("/api/auth/passkey/options")
async def passkey_options(request: Request, response: Response):
    _enabled()
    rate_limit(request, "passkey", 20, 60)
    opts, chal = await passkeys.authentication_options(request, None, require_uv=True)
    await challenges.put(request, response, "wa_login", {"challenge": chal})
    return opts


@router.post("/api/auth/key/options")
async def key_options(request: Request, response: Response, body: dict = Body(...)):
    """Security-key sign-in for a known email. Unknown emails get decoy options so
    the response doesn't reveal which emails have accounts."""
    _enabled()
    rate_limit(request, "passkey", 20, 60)
    email = crypto.norm_email(str(body.get("email", "")))
    async with db.pool.acquire() as c:
        uid = await c.fetchval("SELECT id FROM users WHERE email_idx = $1", crypto.index("email", email))
        rows = await c.fetch("SELECT credential_id, transports FROM webauthn_credentials WHERE user_id = $1", uid) if uid else []
    if not rows:
        rows = [{"credential_id": os.urandom(32), "transports": ["usb", "nfc"]}]
    opts, chal = await passkeys.authentication_options(request, rows, require_uv=True)
    await challenges.put(request, response, "wa_login", {"challenge": chal}, uid)
    return opts


@router.post("/api/auth/passkey/verify")
async def passkey_verify(request: Request, response: Response, body: dict = Body(...)):
    """Finishes passkey and security-key sign-in. User verification (PIN or
    biometric) is required, so this counts as both steps."""
    _enabled()
    rate_limit(request, "passkey", 20, 60)
    data, uid = await challenges.take(request, response, "wa_login")
    row, _ = await passkeys.verify_authentication(request, body.get("credential") or {}, data["challenge"], True, uid)
    await create_session(request, response, row["user_id"])
    return {"ok": True}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    _enabled()
    s = await current_session(request)
    if s:
        await log_event(s["user_id"], "signed_out", request)
    await end_session(request, response)
    return {"ok": True}


# ======================= public: Google / Facebook / Discord =======================

@router.get("/api/auth/oauth/{provider}/start")
async def oauth_start(provider: str, request: Request, mode: str = "login"):
    _enabled()
    p = settings.providers.get(provider)
    if not p:
        raise HTTPException(404, "That sign-in option isn't set up on this server.")
    uid = None
    if mode in ("link", "reauth"):
        uid = (await require_session(request))["user_id"]
    elif mode != "login":
        raise HTTPException(400, "Unknown mode.")
    url, state = oauth.start(request, p)
    resp = RedirectResponse(url, status_code=303)
    await challenges.put(request, resp, "oauth", {**state, "provider": provider, "mode": mode}, uid, minutes=10)
    return resp


def _back(result: str, message: str = "") -> RedirectResponse:
    return RedirectResponse(f"/#auth={result}" + (f"&msg={quote(message)}" if message else ""), status_code=303)


@router.get("/api/auth/oauth/{provider}/callback")
async def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
    _enabled()
    p = settings.providers.get(provider)
    resp = _back("error", "Sign-in was cancelled.")
    try:
        data, link_uid = await challenges.take(request, resp, "oauth")
    except HTTPException:
        return _back("error", "That sign-in link expired. Try again.")
    if not p or error or not code or data.get("provider") != provider or not hmac.compare_digest(data["state"], state):
        return _back("error", "Sign-in was cancelled.")
    try:
        info = await oauth.finish(request, p, code, data["verifier"])
    except HTTPException as e:
        return _back("error", e.detail)

    subject_idx = crypto.index(f"oauth:{provider}", str(info["subject"]))
    async with db.pool.acquire() as c:
        owner = await c.fetchval("SELECT user_id FROM oauth_identities WHERE provider = $1 AND subject_idx = $2",
                                 provider, subject_idx)
    mode = data["mode"]

    if mode == "reauth":
        if owner != link_uid:
            return _back("error", f"That {p.label} account isn't connected to yours.")
        resp = _back("reauth")
        await _mark_verified(request)
        return resp

    if mode == "link":
        if owner and owner != link_uid:
            return _back("error", f"That {p.label} account is already connected to another user.")
        if not owner:
            async with db.pool.acquire() as c:
                await c.execute("""INSERT INTO oauth_identities (provider, subject_idx, user_id, email_enc) VALUES ($1,$2,$3,$4)
                                   ON CONFLICT (user_id, provider) DO UPDATE SET subject_idx = EXCLUDED.subject_idx,
                                   email_enc = EXCLUDED.email_enc""",
                                provider, subject_idx, link_uid,
                                crypto.seal(info.get("email"), f"oauth_identities.email:{link_uid}"))
            await log_event(link_uid, "identity_linked", request)
        return _back("linked", f"{p.label} connected.")

    # mode == "login"
    resp = _back("ok")
    if owner:
        result = await _sign_in(request, resp, await _user(owner))
        if result.get("second_step"):
            resp.headers["location"] = "/#auth=second&methods=" + ",".join(result["second_step"])
        return resp

    if not settings.allow_registration:
        return _back("error", "New accounts are switched off on this server.")
    email = crypto.norm_email(info["email"]) if info.get("email") and info.get("email_verified") else None
    if email:
        async with db.pool.acquire() as c:
            if await c.fetchval("SELECT 1 FROM users WHERE email_idx = $1", crypto.index("email", email)):
                # Never auto-link by email: that would let anyone who controls a
                # provider account with your address take over your account.
                return _back("error", f"An account already uses this email. Sign in, then connect {p.label} under Security.")
    async with db.pool.acquire() as c, c.transaction():
        uid = await c.fetchval("INSERT INTO users (webauthn_handle) VALUES ($1) RETURNING id", os.urandom(32))
        await c.execute("UPDATE users SET email_idx=$2, email_enc=$3, name_enc=$4 WHERE id=$1",
                        uid, crypto.index("email", email) if email else None,
                        crypto.seal(email, f"users.email:{uid}") if email else None,
                        crypto.seal((info.get("name") or (email or p.label + " user").split("@")[0])[:80], f"users.name:{uid}"))
        await c.execute("INSERT INTO oauth_identities (provider, subject_idx, user_id, email_enc) VALUES ($1,$2,$3,$4)",
                        provider, subject_idx, uid, crypto.seal(info.get("email"), f"oauth_identities.email:{uid}"))
    await log_event(uid, "registered", request)
    await create_session(request, resp, uid)
    return resp


# ======================= me: profile + settings =======================

@router.get("/api/me")
async def me(request: Request):
    if not settings.enabled or not db.pool:
        return {"user": None}
    s = await current_session(request)
    if not s:
        return {"user": None}
    u = await _user(s["user_id"])
    return {"user": {"name": _name(u), "email": _email(u), "has_password": u["password_hash"] is not None,
                     "created_at": u["created_at"].isoformat()}}


@router.patch("/api/me")
async def update_me(request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    uid = s["user_id"]
    if "name" in body:
        name = str(body["name"]).strip()[:80]
        if not name:
            raise HTTPException(400, "Enter a name.")
        async with db.pool.acquire() as c:
            await c.execute("UPDATE users SET name_enc = $2 WHERE id = $1", uid, crypto.seal(name, f"users.name:{uid}"))
    if "email" in body:
        await require_step_up(request)
        email = crypto.norm_email(str(body["email"]))
        if not EMAIL_RE.match(email):
            raise HTTPException(400, "Enter a valid email address.")
        async with db.pool.acquire() as c:
            taken = await c.fetchval("SELECT id FROM users WHERE email_idx = $1", crypto.index("email", email))
            if taken and taken != uid:
                raise HTTPException(409, "Another account already uses that email.")
            await c.execute("UPDATE users SET email_idx = $2, email_enc = $3 WHERE id = $1",
                            uid, crypto.index("email", email), crypto.seal(email, f"users.email:{uid}"))
        await log_event(uid, "email_changed", request)
    return {"ok": True}


@router.get("/api/me/settings")
async def get_settings(request: Request):
    _enabled()
    s = await require_session(request)
    u = await _user(s["user_id"])
    return {"settings": crypto.open_json(u["settings_enc"], f"users.settings:{u['id']}")}


@router.put("/api/me/settings")
async def put_settings(request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    blob = body.get("settings")
    if not isinstance(blob, dict) or len(json.dumps(blob)) > SETTINGS_MAX:
        raise HTTPException(400, "Settings too large.")
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET settings_enc = $2 WHERE id = $1",
                        s["user_id"], crypto.seal_json(blob, f"users.settings:{s['user_id']}"))
    return {"ok": True}


@router.delete("/api/me")
async def delete_me(request: Request, response: Response):
    _enabled()
    s = await require_step_up(request)
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM users WHERE id = $1", s["user_id"])  # cascades to everything
    await end_session(request, response)
    return {"ok": True}


# ======================= me: security overview =======================

@router.get("/api/me/security")
async def security(request: Request):
    _enabled()
    s = await require_session(request)
    uid = s["user_id"]
    u = await _user(uid)
    async with db.pool.acquire() as c:
        creds = await c.fetch("SELECT * FROM webauthn_credentials WHERE user_id = $1 ORDER BY created_at", uid)
        idents = await c.fetch("SELECT provider, email_enc, created_at FROM oauth_identities WHERE user_id = $1", uid)
        sess = await c.fetch("SELECT * FROM sessions WHERE user_id = $1 AND expires_at > now() ORDER BY last_seen_at DESC", uid)
        codes = await c.fetchval("SELECT count(*) FROM backup_codes WHERE user_id = $1 AND used_at IS NULL", uid)
        events = await c.fetch("SELECT event, detail_enc, at FROM security_events WHERE user_id = $1 ORDER BY at DESC LIMIT 20", uid)

    def mask_ip(ip: str | None) -> str:
        if not ip:
            return ""
        return ".".join(ip.split(".")[:2]) + ".x.x" if "." in ip else ip.split(":")[0] + ":…"

    linked = {i["provider"]: i for i in idents}
    return {
        "has_password": u["password_hash"] is not None,
        "totp_enabled": u["totp_enabled"],
        "backup_codes_left": codes,
        "verified_recently": now() - s["verified_at"] < timedelta(minutes=STEP_UP_MINUTES),
        "credentials": [{
            "id": str(r["id"]), "kind": r["kind"], "backed_up": r["backed_up"],
            "name": crypto.open_(r["name_enc"], f"webauthn_credentials.name:{uid}") or ("Passkey" if r["kind"] == "passkey" else "Security key"),
            "created_at": r["created_at"].isoformat(), "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
        } for r in creds],
        "providers": [{
            "name": p.name, "label": p.label, "connected": p.name in linked,
            "email": crypto.open_(linked[p.name]["email_enc"], f"oauth_identities.email:{uid}") if p.name in linked else None,
        } for p in settings.providers.values()],
        "sessions": [{
            "id": r["token_hash"].hex()[:16], "current": r["token_hash"] == s["token_hash"],
            "device": crypto.open_(r["device_enc"], f"sessions.device:{uid}"),
            "ip": mask_ip(crypto.open_(r["ip_enc"], f"sessions.ip:{uid}")),
            "created_at": r["created_at"].isoformat(), "last_seen_at": r["last_seen_at"].isoformat(),
        } for r in sess],
        "events": [{
            "event": EVENT_LABELS.get(e["event"], e["event"]), "at": e["at"].isoformat(),
            "detail": crypto.open_(e["detail_enc"], f"security_events.detail:{uid}"),
        } for e in events],
    }


# ======================= me: confirm it's you (step-up) =======================

@router.post("/api/me/reauth")
async def reauth(request: Request, body: dict = Body(...)):
    _enabled()
    rate_limit(request, "reauth", 10, 60)
    s = await require_session(request)
    u = await _user(s["user_id"])
    if "password" in body:
        ok, _ = crypto.verify_password(u["password_hash"], str(body["password"]))
    else:
        ok = await _check_totp(u, str(body.get("code", "")))
    if not ok:
        raise HTTPException(401, "That didn't match. Try again.")
    await _mark_verified(request)
    await log_event(u["id"], "reauth", request)
    return {"ok": True}


@router.post("/api/me/reauth/key/options")
async def reauth_key_options(request: Request, response: Response):
    _enabled()
    s = await require_session(request)
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT credential_id, transports FROM webauthn_credentials WHERE user_id = $1", s["user_id"])
    if not rows:
        raise HTTPException(400, "You have no passkeys or security keys yet.")
    opts, chal = await passkeys.authentication_options(request, rows, require_uv=False)
    await challenges.put(request, response, "wa_reauth", {"challenge": chal}, s["user_id"])
    return opts


@router.post("/api/me/reauth/key/verify")
async def reauth_key_verify(request: Request, response: Response, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    data, uid = await challenges.take(request, response, "wa_reauth")
    if uid != s["user_id"]:
        raise HTTPException(400, "This request expired. Please try again.")
    await passkeys.verify_authentication(request, body.get("credential") or {}, data["challenge"], False, uid)
    await _mark_verified(request)
    await log_event(uid, "reauth", request)
    return {"ok": True}


# ======================= me: password =======================

@router.post("/api/me/password")
async def set_password(request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_step_up(request)
    u = await _user(s["user_id"])
    pw = str(body.get("password", ""))
    _check_password(pw, _email(u))
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET password_hash = $2 WHERE id = $1", u["id"], crypto.hash_password(pw))
        # A new password signs out every other device.
        await c.execute("DELETE FROM sessions WHERE user_id = $1 AND token_hash <> $2", u["id"], s["token_hash"])
    await log_event(u["id"], "password_set", request)
    return {"ok": True}


# ======================= me: authenticator app + backup codes =======================

@router.post("/api/me/totp/setup")
async def totp_setup(request: Request):
    _enabled()
    s = await require_step_up(request)
    u = await _user(s["user_id"])
    if u["totp_enabled"]:
        raise HTTPException(400, "The authenticator app is already on.")
    secret = pyotp.random_base32(32)
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET totp_secret_enc = $2, totp_last_step = NULL WHERE id = $1",
                        u["id"], crypto.seal(secret, f"users.totp:{u['id']}"))
    label = _email(u) or _name(u) or "account"
    uri = pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name="Aggregator")
    qr = segno.make(uri, error="m").svg_inline(scale=5, dark="#000", light="#fff", border=2)
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "uri": uri, "qr_svg": qr}


@router.post("/api/me/totp/enable")
async def totp_enable(request: Request, body: dict = Body(...)):
    _enabled()
    rate_limit(request, "reauth", 10, 60)
    s = await require_step_up(request)
    u = await _user(s["user_id"])
    if not u["totp_secret_enc"] or u["totp_enabled"]:
        raise HTTPException(400, "Start the set-up again.")
    if not await _check_totp(u, str(body.get("code", ""))):
        raise HTTPException(400, "That code didn't match. Check your phone's clock and try the newest code.")
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET totp_enabled = true WHERE id = $1", u["id"])
    codes = await _store_backup_codes(u["id"])
    await log_event(u["id"], "totp_enabled", request)
    return {"backup_codes": codes}


@router.post("/api/me/totp/disable")
async def totp_disable(request: Request):
    _enabled()
    s = await require_step_up(request)
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET totp_enabled = false, totp_secret_enc = NULL, totp_last_step = NULL WHERE id = $1",
                        s["user_id"])
        has_keys = await c.fetchval("SELECT count(*) FROM webauthn_credentials WHERE user_id = $1 AND kind = 'security_key'", s["user_id"])
        if not has_keys:
            await c.execute("DELETE FROM backup_codes WHERE user_id = $1", s["user_id"])
    await log_event(s["user_id"], "totp_disabled", request)
    return {"ok": True}


@router.post("/api/me/backup-codes")
async def backup_codes(request: Request):
    _enabled()
    s = await require_step_up(request)
    codes = await _store_backup_codes(s["user_id"])
    await log_event(s["user_id"], "backup_codes", request)
    return {"backup_codes": codes}


# ======================= me: passkeys + security keys =======================

@router.post("/api/me/webauthn/options")
async def webauthn_register_options(request: Request, response: Response, body: dict = Body(...)):
    _enabled()
    s = await require_step_up(request)
    kind = body.get("kind")
    if kind not in ("passkey", "security_key"):
        raise HTTPException(400, "Unknown key type.")
    u = await _user(s["user_id"])
    opts, chal = await passkeys.registration_options(request, u, kind, _email(u) or _name(u) or "account")
    await challenges.put(request, response, "wa_register", {"challenge": chal, "kind": kind}, u["id"])
    return opts


@router.post("/api/me/webauthn/verify")
async def webauthn_register_verify(request: Request, response: Response, body: dict = Body(...)):
    _enabled()
    s = await require_step_up(request)
    data, uid = await challenges.take(request, response, "wa_register")
    if uid != s["user_id"]:
        raise HTTPException(400, "This request expired. Please try again.")
    v = await passkeys.verify_registration(request, uid, body.get("credential") or {}, data["challenge"], data["kind"])
    name = str(body.get("name", "")).strip()[:60] or ("Passkey" if data["kind"] == "passkey" else "Security key")
    async with db.pool.acquire() as c:
        try:
            await c.execute("""INSERT INTO webauthn_credentials
                (user_id, credential_id, public_key, sign_count, kind, name_enc, transports, backed_up)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                uid, v["credential_id"], v["public_key"], v["sign_count"], data["kind"],
                crypto.seal(name, f"webauthn_credentials.name:{uid}"), v["transports"], v["backed_up"])
        except Exception:
            raise HTTPException(409, "That key is already registered.")
    # The first security key turns on two-step sign-in, so it needs backup codes.
    first_codes = None
    if data["kind"] == "security_key":
        async with db.pool.acquire() as c:
            if not await c.fetchval("SELECT count(*) FROM backup_codes WHERE user_id = $1 AND used_at IS NULL", uid):
                first_codes = await _store_backup_codes(uid)
    await log_event(uid, "passkey_added" if data["kind"] == "passkey" else "security_key_added", request)
    return {"ok": True, "backup_codes": first_codes}


@router.patch("/api/me/webauthn/{cred_id}")
async def webauthn_rename(cred_id: UUID, request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    name = str(body.get("name", "")).strip()[:60]
    if not name:
        raise HTTPException(400, "Enter a name.")
    async with db.pool.acquire() as c:
        done = await c.execute("UPDATE webauthn_credentials SET name_enc = $3 WHERE id = $1 AND user_id = $2",
                               cred_id, s["user_id"], crypto.seal(name, f"webauthn_credentials.name:{s['user_id']}"))
    if done.endswith(" 0"):
        raise HTTPException(404, "Not found.")
    return {"ok": True}


@router.delete("/api/me/webauthn/{cred_id}")
async def webauthn_delete(cred_id: UUID, request: Request):
    _enabled()
    s = await require_step_up(request)
    if await _sign_in_methods(s["user_id"]) <= 1:
        raise HTTPException(400, "This is your only way to sign in. Add a password or another key first.")
    async with db.pool.acquire() as c:
        done = await c.execute("DELETE FROM webauthn_credentials WHERE id = $1 AND user_id = $2", cred_id, s["user_id"])
    if done.endswith(" 0"):
        raise HTTPException(404, "Not found.")
    await log_event(s["user_id"], "credential_removed", request)
    return {"ok": True}


# ======================= me: connected accounts =======================

@router.delete("/api/me/identities/{provider}")
async def unlink(provider: str, request: Request):
    _enabled()
    s = await require_step_up(request)
    if await _sign_in_methods(s["user_id"]) <= 1:
        raise HTTPException(400, "This is your only way to sign in. Add a password or a passkey first.")
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM oauth_identities WHERE user_id = $1 AND provider = $2", s["user_id"], provider)
    await log_event(s["user_id"], "identity_unlinked", request)
    return {"ok": True}


# ======================= me: signed-in devices =======================

@router.delete("/api/me/sessions/{sid}")
async def revoke_session(sid: str, request: Request):
    _enabled()
    s = await require_session(request)
    if not re.fullmatch(r"[0-9a-f]{16}", sid):
        raise HTTPException(400, "Bad id.")
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM sessions WHERE user_id = $1 AND encode(token_hash, 'hex') LIKE $2",
                        s["user_id"], sid + "%")
    return {"ok": True}


@router.post("/api/me/sessions/revoke-others")
async def revoke_others(request: Request):
    _enabled()
    s = await require_session(request)
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM sessions WHERE user_id = $1 AND token_hash <> $2", s["user_id"], s["token_hash"])
    await log_event(s["user_id"], "sessions_revoked", request)
    return {"ok": True}
