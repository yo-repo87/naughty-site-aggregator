"""Sessions, request helpers, rate limiting and the security-event log.

Session cookie: 256-bit random token, HttpOnly, SameSite=Lax, and on HTTPS
`Secure` with the `__Host-` prefix (pinned to this exact host). The database
only stores an HMAC of the token, so a database leak can't be replayed.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, Request, Response

from . import crypto, db
from .config import settings

SESSION_DAYS = 30
STEP_UP_MINUTES = 15          # how long a "confirm it's you" lasts for sensitive changes
PENDING_MINUTES = 5           # time allowed for the second sign-in step
TOUCH_EVERY = timedelta(minutes=5)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- request helpers ----------

def is_https(request: Request) -> bool:
    # Behind Cloudflare -> tunnel -> NPM the app sees plain http, so the public
    # hostname itself is the signal that the browser is on HTTPS.
    host = (request.headers.get("host") or "").split(":")[0]
    return request.url.scheme == "https" or (bool(settings.public_host) and host == settings.public_host
                                             and settings.public_url.startswith("https://"))


def cookie_name(request: Request, base: str) -> str:
    return f"__Host-{base}" if is_https(request) else base


def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")


def device_summary(request: Request) -> str:
    ua = request.headers.get("user-agent", "")
    browser = next((b for b in ("Edg", "OPR", "Firefox", "Chrome", "Safari") if b in ua), "Browser")
    browser = {"Edg": "Edge", "OPR": "Opera"}.get(browser, browser)
    os_ = next((o for o in ("iPhone", "iPad", "Android", "Windows", "Mac OS X", "Linux", "CrOS") if o in ua), "Unknown OS")
    os_ = {"Mac OS X": "macOS", "CrOS": "ChromeOS"}.get(os_, os_)
    return f"{browser} on {os_}"


def set_cookie(request: Request, response: Response, base: str, value: str, max_age: int) -> None:
    response.set_cookie(cookie_name(request, base), value, max_age=max_age, path="/",
                        httponly=True, secure=is_https(request), samesite="lax")


def clear_cookie(request: Request, response: Response, base: str) -> None:
    response.delete_cookie(cookie_name(request, base), path="/", secure=is_https(request), samesite="lax")


# ---------- rate limiting (per process, in memory) ----------

_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request, bucket: str, limit: int, per_seconds: int) -> None:
    key = f"{bucket}:{client_ip(request)}"
    q, t = _hits[key], time.monotonic()
    while q and t - q[0] > per_seconds:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many attempts. Wait a minute and try again.")
    q.append(t)


# ---------- sessions ----------

async def create_session(request: Request, response: Response, user_id: UUID) -> None:
    tok = crypto.token()
    expires = now() + timedelta(days=SESSION_DAYS)
    async with db.pool.acquire() as c:
        await c.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, device_enc, ip_enc) VALUES ($1,$2,$3,$4,$5)",
            crypto.index("session", tok), user_id, expires,
            crypto.seal(device_summary(request), f"sessions.device:{user_id}"),
            crypto.seal(client_ip(request), f"sessions.ip:{user_id}"),
        )
        await c.execute("UPDATE users SET last_login_at = now(), failed_logins = 0, locked_until = NULL WHERE id = $1", user_id)
    set_cookie(request, response, "agg_session", tok, SESSION_DAYS * 86400)
    clear_cookie(request, response, "agg_pending")
    await log_event(user_id, "signed_in", request)


async def current_session(request: Request):
    tok = request.cookies.get(cookie_name(request, "agg_session"))
    if not tok or not db.pool:
        return None
    th = crypto.index("session", tok)
    async with db.pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM sessions WHERE token_hash = $1 AND expires_at > now()", th)
        if row and now() - row["last_seen_at"] > TOUCH_EVERY:
            await c.execute("UPDATE sessions SET last_seen_at = now() WHERE token_hash = $1", th)
    return row


async def require_session(request: Request):
    s = await current_session(request)
    if not s:
        raise HTTPException(401, "Sign in first.")
    return s


async def require_step_up(request: Request):
    """Sensitive changes need a recent proof (sign-in or 'confirm it's you')."""
    s = await require_session(request)
    if now() - s["verified_at"] > timedelta(minutes=STEP_UP_MINUTES):
        raise HTTPException(403, detail={"reauth": True, "message": "Confirm it's you to continue."})
    return s


async def end_session(request: Request, response: Response) -> None:
    tok = request.cookies.get(cookie_name(request, "agg_session"))
    if tok and db.pool:
        async with db.pool.acquire() as c:
            await c.execute("DELETE FROM sessions WHERE token_hash = $1", crypto.index("session", tok))
    clear_cookie(request, response, "agg_session")


# ---------- pending second step ----------

async def start_pending(request: Request, response: Response, user_id: UUID) -> None:
    tok = crypto.token()
    async with db.pool.acquire() as c:
        await c.execute("INSERT INTO pending_logins (token_hash, user_id, expires_at) VALUES ($1,$2,$3)",
                        crypto.index("pending", tok), user_id, now() + timedelta(minutes=PENDING_MINUTES))
    set_cookie(request, response, "agg_pending", tok, PENDING_MINUTES * 60)


async def pending_user(request: Request) -> UUID:
    tok = request.cookies.get(cookie_name(request, "agg_pending"))
    if tok:
        async with db.pool.acquire() as c:
            uid = await c.fetchval("SELECT user_id FROM pending_logins WHERE token_hash = $1 AND expires_at > now()",
                                   crypto.index("pending", tok))
        if uid:
            return uid
    raise HTTPException(401, "Your sign-in timed out. Start again.")


async def finish_pending(request: Request) -> None:
    tok = request.cookies.get(cookie_name(request, "agg_pending"))
    if tok:
        async with db.pool.acquire() as c:
            await c.execute("DELETE FROM pending_logins WHERE token_hash = $1 OR expires_at < now()",
                            crypto.index("pending", tok))


# ---------- security events ----------

async def log_event(user_id: UUID, event: str, request: Request | None = None) -> None:
    detail = f"{device_summary(request)} · {client_ip(request)}" if request else None
    async with db.pool.acquire() as c:
        await c.execute("INSERT INTO security_events (user_id, event, detail_enc) VALUES ($1,$2,$3)",
                        user_id, event, crypto.seal(detail, f"security_events.detail:{user_id}"))
        # keep the log bounded
        await c.execute("""DELETE FROM security_events WHERE user_id = $1 AND id NOT IN
                           (SELECT id FROM security_events WHERE user_id = $1 ORDER BY at DESC LIMIT 100)""", user_id)
