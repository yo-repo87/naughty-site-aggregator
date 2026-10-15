"""HTTP hardening applied to every request.

- REQUIRE_HTTPS: anything not arriving on the public HTTPS host is redirected
  there (localhost is exempt for health checks and development).
- Security headers: strict CSP, HSTS on HTTPS, no framing, no referrer, nosniff.
- Cross-site request blocking: state-changing /api calls must come from this
  site (Origin / Sec-Fetch-Site), on top of SameSite cookies.
- Account responses are never cached.
- REQUIRE_LOGIN: searching needs a signed-in session.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import db
from .config import settings
from .sessions import current_session, is_https

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",   # element style attributes (badge colours)
    "img-src 'self' https: data: blob:",  # thumbnails come from each site's CDN
    "media-src 'self' https: blob:",      # hover previews
    "frame-src https:",                   # each site's embed player
    "connect-src 'self'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
])
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")
LOGIN_GATED = ("/api/search", "/api/thumb")


class Hardening(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        path = request.url.path

        if settings.require_https and settings.public_host and host != settings.public_host and host not in LOCAL_HOSTS:
            target = settings.public_url + path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(target, status_code=308)

        if request.method in UNSAFE and path.startswith("/api/"):
            origin = request.headers.get("origin")
            site = request.headers.get("sec-fetch-site")
            own = {f"{request.url.scheme}://{request.headers.get('host')}", f"https://{request.headers.get('host')}"}
            if (origin and origin not in own) or site == "cross-site":
                return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)

        if settings.require_login and settings.enabled and db.pool and path.startswith(LOGIN_GATED):
            if not await current_session(request):
                return JSONResponse({"detail": "Sign in to search.", "login_required": True}, status_code=401)

        response = await call_next(request)
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if is_https(request):
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path.startswith(("/api/me", "/api/auth")):
            h["Cache-Control"] = "no-store"
        return response


def install(app: FastAPI) -> None:
    app.add_middleware(Hardening)
