"""Google / Facebook / Discord sign-in (OAuth 2.0 authorization code + PKCE).

State, PKCE verifier and mode (sign in vs. link to the current account) are kept
server-side (challenges.py); the browser only carries a random handle.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession
from fastapi import HTTPException, Request

from .config import Provider, settings


def redirect_uri(request: Request, provider: str) -> str:
    base = settings.public_url or f"{request.url.scheme}://{request.headers.get('host')}"
    return f"{base}/api/auth/oauth/{provider}/callback"


def start(request: Request, p: Provider) -> tuple[str, dict]:
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "client_id": p.client_id, "redirect_uri": redirect_uri(request, p.name), "response_type": "code",
        "scope": p.scope, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if p.name == "google":
        params["prompt"] = "select_account"
    return f"{p.authorize_url}?{urlencode(params)}", {"state": state, "verifier": verifier}


async def finish(request: Request, p: Provider, code: str, verifier: str) -> dict:
    """Exchange the code and return {subject, email, email_verified, name}."""
    form = {
        "client_id": p.client_id, "client_secret": p.client_secret, "code": code,
        "redirect_uri": redirect_uri(request, p.name), "grant_type": "authorization_code", "code_verifier": verifier,
    }
    async with AsyncSession(timeout=15) as s:
        if p.name == "facebook":
            r = await s.get(p.token_url, params=form)
        else:
            r = await s.post(p.token_url, data=form, headers={"Accept": "application/json"})
        if r.status_code != 200:
            raise HTTPException(400, f"{p.label} sign-in failed (token exchange).")
        access = json.loads(r.content).get("access_token")
        if not access:
            raise HTTPException(400, f"{p.label} sign-in failed (no token).")
        u = await s.get(p.userinfo_url, headers={"Authorization": f"Bearer {access}"})
        if u.status_code != 200:
            raise HTTPException(400, f"{p.label} sign-in failed (profile).")
        info = json.loads(u.content)

    if p.name == "google":
        return {"subject": info["sub"], "email": info.get("email"),
                "email_verified": bool(info.get("email_verified")), "name": info.get("name")}
    if p.name == "discord":
        return {"subject": info["id"], "email": info.get("email"), "email_verified": bool(info.get("verified")),
                "name": info.get("global_name") or info.get("username")}
    # Facebook only returns confirmed email addresses.
    return {"subject": info["id"], "email": info.get("email"), "email_verified": bool(info.get("email")),
            "name": info.get("name")}
