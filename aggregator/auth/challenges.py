"""One-time, short-lived server-side state (WebAuthn challenges, OAuth state).

The browser only holds a random handle in an HttpOnly cookie; the data itself
is encrypted in the database and deleted when used.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException, Request, Response

from . import crypto, db
from .sessions import clear_cookie, cookie_name, now, set_cookie


async def put(request: Request, response: Response, purpose: str, data: dict,
              user_id: UUID | None = None, minutes: int = 5) -> None:
    handle = crypto.token()
    hh = crypto.index("challenge", handle)
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM auth_challenges WHERE expires_at < now()")
        await c.execute(
            "INSERT INTO auth_challenges (handle_hash, purpose, user_id, data_enc, expires_at) VALUES ($1,$2,$3,$4,$5)",
            hh, purpose, user_id, crypto.seal_json(data, f"auth_challenges:{purpose}"), now() + timedelta(minutes=minutes),
        )
    set_cookie(request, response, f"agg_{purpose}", handle, minutes * 60)


async def take(request: Request, response: Response, purpose: str) -> tuple[dict, UUID | None]:
    """Fetch and delete the state for `purpose`; fails if missing, expired or reused."""
    handle = request.cookies.get(cookie_name(request, f"agg_{purpose}"))
    clear_cookie(request, response, f"agg_{purpose}")
    if handle:
        async with db.pool.acquire() as c:
            row = await c.fetchrow(
                "DELETE FROM auth_challenges WHERE handle_hash = $1 AND purpose = $2 RETURNING data_enc, user_id, expires_at",
                crypto.index("challenge", handle), purpose)
        if row and row["expires_at"] > now():
            return crypto.open_json(row["data_enc"], f"auth_challenges:{purpose}"), row["user_id"]
    raise HTTPException(400, "This request expired. Please try again.")
