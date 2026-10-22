"""Gotify — optional send-to / alert plug-in: push notifications from your own Gotify server."""
from __future__ import annotations

from aggregator.net import NetError, as_json, clean_url, guarded_request

KIND = "gotify"
LABEL = "Gotify"
DESCRIPTION = "Push notification through your Gotify server (Apps → create an application → its token). Tapping it opens the link."
FIELDS = [
    {"name": "url", "label": "Gotify address", "type": "url", "required": True, "placeholder": "https://gotify.example.com"},
    {"name": "token", "label": "Application token", "type": "password", "required": True},
    {"name": "priority", "label": "Priority (0-10, optional)", "type": "text", "placeholder": "5"},
]
OPTIONS = []


def _base(cfg: dict) -> str:
    return clean_url(cfg["url"], example="https://gotify.example.com").rstrip("/")


async def _post(cfg: dict, title: str, message: str, link: str) -> None:
    try:
        priority = max(0, min(10, int(cfg.get("priority") or 5)))
    except ValueError:
        raise NetError("Priority is a number from 0 to 10.") from None
    body = {"title": title[:250], "message": message[:3000], "priority": priority}
    if link:
        body["extras"] = {"client::notification": {"click": {"url": link}}}
    r = await guarded_request("POST", _base(cfg) + "/message", json_body=body,
                              headers={"X-Gotify-Key": cfg.get("token") or ""}, what="Gotify")
    if r.status_code != 200:
        raise NetError(f"Gotify answered HTTP {r.status_code}: {str((as_json(r) or {}).get('errorDescription') or '')[:200]}")


async def check(cfg: dict) -> str:
    cfg["url"] = _base(cfg)
    r = await guarded_request("GET", cfg["url"] + "/version", what="Gotify")
    info = as_json(r) if r.status_code == 200 else None
    if not info or "version" not in info:
        raise NetError("That address doesn't answer like a Gotify server.")
    await _post(cfg, "Aggregator connected", "Notifications from Aggregator will arrive here.", "")
    return f"Gotify {str(info['version'])[:20]} · test sent"


async def send(cfg: dict, video: dict, options: dict) -> str:
    await _post(cfg, video["title"], video["url"], video["url"])
    return "Sent to Gotify."


async def notify(cfg: dict, title: str, message: str, link: str) -> None:
    await _post(cfg, title, message, link)
