"""Generic webhook: POSTs JSON to any URL (Home Assistant, n8n, a yt-dlp wrapper script...)."""
from __future__ import annotations

from ..net import NetError, clean_url, guarded_request

KIND = "webhook"
LABEL = "Webhook"
DESCRIPTION = ('POSTs JSON to your URL: {"event": "video", "url", "title", "source", "duration", "thumb"} '
               'for videos and {"event": "alert", "title", "message", "link"} for saved-search alerts.')
FIELDS = [
    {"name": "url", "label": "Webhook URL", "type": "url", "required": True, "placeholder": "https://n8n.example.com/webhook/…"},
    {"name": "header_name", "label": "Header name (optional)", "type": "text", "placeholder": "Authorization"},
    {"name": "header_value", "label": "Header value", "type": "password"},
]
OPTIONS = []


def _headers(cfg: dict) -> dict:
    name = (cfg.get("header_name") or "").strip()
    if name and not all(c.isalnum() or c in "-_" for c in name):
        raise NetError("The header name can only contain letters, digits, - and _.")
    return {name: cfg.get("header_value") or ""} if name else {}


async def _post(cfg: dict, body: dict) -> None:
    url = clean_url(cfg["url"], keep_query=True)
    r = await guarded_request("POST", url, json_body=body, headers=_headers(cfg), what="the webhook")
    if not 200 <= r.status_code < 300:
        raise NetError(f"The webhook answered HTTP {r.status_code}.")


async def check(cfg: dict) -> str:
    cfg["url"] = clean_url(cfg["url"], keep_query=True)
    await _post(cfg, {"event": "test", "message": "Connected to Aggregator."})
    return "Test event delivered"


async def send(cfg: dict, video: dict, options: dict) -> str:
    await _post(cfg, {"event": "video", **{k: video.get(k) for k in ("url", "title", "source", "duration", "thumb")}})
    return "Sent to the webhook."


async def notify(cfg: dict, title: str, message: str, link: str) -> None:
    await _post(cfg, {"event": "alert", "title": title, "message": message, "link": link})
