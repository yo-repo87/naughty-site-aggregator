"""linkding — optional send-to plug-in: saves the video as a bookmark in your linkding."""
from __future__ import annotations

import re

from aggregator.net import NetError, as_json, clean_url, guarded_request

KIND = "linkding"
LABEL = "linkding"
DESCRIPTION = "Saves the video as a bookmark (marked unread) in your linkding. Token: linkding → Settings → Integrations → REST API."
FIELDS = [
    {"name": "url", "label": "linkding address", "type": "url", "required": True, "placeholder": "https://links.example.com"},
    {"name": "token", "label": "API token", "type": "password", "required": True},
    {"name": "tags", "label": "Tags (optional)", "type": "text", "placeholder": "aggregator, watch-later"},
]
OPTIONS = []
TAG_RE = re.compile(r"[^\w-]")


def _base(cfg: dict) -> str:
    return clean_url(cfg["url"], example="https://links.example.com").rstrip("/")


def _headers(cfg: dict) -> dict:
    return {"Authorization": f"Token {cfg.get('token') or ''}"}


async def check(cfg: dict) -> str:
    cfg["url"] = _base(cfg)
    r = await guarded_request("GET", cfg["url"] + "/api/bookmarks/?limit=1", headers=_headers(cfg), what="linkding")
    data = as_json(r) if r.status_code == 200 else None
    if not data or "results" not in data:
        raise NetError(f"That address answered (HTTP {r.status_code}), but not like linkding's API does.")
    return f"linkding · {data.get('count', 0)} bookmarks"


async def send(cfg: dict, video: dict, options: dict) -> str:
    tags = [TAG_RE.sub("", t.strip().replace(" ", "-"))[:60] for t in (cfg.get("tags") or "").split(",")]
    body = {"url": video["url"], "title": video["title"], "tag_names": [t for t in tags if t] or ["aggregator"], "unread": True}
    r = await guarded_request("POST", _base(cfg) + "/api/bookmarks/", json_body=body, headers=_headers(cfg), what="linkding")
    if r.status_code not in (200, 201):
        raise NetError(f"linkding didn't save it (HTTP {r.status_code}).")
    return "Bookmarked in linkding."
