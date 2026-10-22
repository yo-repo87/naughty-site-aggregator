"""Karakeep (formerly Hoarder) — optional send-to plug-in: saves the video link to your Karakeep."""
from __future__ import annotations

from aggregator.net import NetError, as_json, clean_url, guarded_request

KIND = "karakeep"
LABEL = "Karakeep"
DESCRIPTION = "Saves the video link to your Karakeep / Hoarder. API key: Karakeep → Settings → API Keys."
FIELDS = [
    {"name": "url", "label": "Karakeep address", "type": "url", "required": True, "placeholder": "https://karakeep.example.com"},
    {"name": "token", "label": "API key", "type": "password", "required": True},
]
OPTIONS = []


def _base(cfg: dict) -> str:
    return clean_url(cfg["url"], example="https://karakeep.example.com").rstrip("/")


def _headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg.get('token') or ''}"}


async def check(cfg: dict) -> str:
    cfg["url"] = _base(cfg)
    r = await guarded_request("GET", cfg["url"] + "/api/v1/users/me", headers=_headers(cfg), what="Karakeep")
    me = as_json(r) if r.status_code == 200 else None
    if not me or "id" not in me:
        raise NetError(f"That address answered (HTTP {r.status_code}), but not like Karakeep's API does.")
    return f"Karakeep · {str(me.get('name') or me.get('email') or 'connected')[:60]}"


async def send(cfg: dict, video: dict, options: dict) -> str:
    r = await guarded_request("POST", _base(cfg) + "/api/v1/bookmarks", headers=_headers(cfg),
                              json_body={"type": "link", "url": video["url"], "title": video["title"][:250]}, what="Karakeep")
    if r.status_code not in (200, 201):
        raise NetError(f"Karakeep didn't save it (HTTP {r.status_code}).")
    return "Saved to Karakeep."
