"""Pushover — optional send-to / alert plug-in: push notifications via pushover.net."""
from __future__ import annotations

import re

from aggregator.net import NetError, as_json, guarded_request

KIND = "pushover"
LABEL = "Pushover"
DESCRIPTION = "Push notification through Pushover. Needs your user key and an application token (pushover.net → Create an Application)."
FIELDS = [
    {"name": "user", "label": "User key", "type": "password", "required": True},
    {"name": "token", "label": "Application token", "type": "password", "required": True},
    {"name": "device", "label": "Device (optional)", "type": "text", "placeholder": "all devices"},
]
OPTIONS = []
API = "https://api.pushover.net/1"
KEY_RE = re.compile(r"^[A-Za-z0-9]{30}$")


def _form(cfg: dict) -> dict:
    user, token = (cfg.get("user") or "").strip(), (cfg.get("token") or "").strip()
    if not KEY_RE.match(user) or not KEY_RE.match(token):
        raise NetError("Pushover keys are 30 letters and digits: check the user key and application token.")
    form = {"user": user, "token": token}
    if (cfg.get("device") or "").strip():
        form["device"] = cfg["device"].strip()[:25]
    return form


async def _post(cfg: dict, path: str, extra: dict) -> None:
    r = await guarded_request("POST", f"{API}/{path}", data={**_form(cfg), **extra}, what="Pushover")
    data = as_json(r) or {}
    if data.get("status") != 1:
        raise NetError("Pushover said: " + "; ".join(map(str, data.get("errors") or [r.status_code]))[:200])


async def check(cfg: dict) -> str:
    await _post(cfg, "users/validate.json", {})
    return "Pushover keys accepted"


async def send(cfg: dict, video: dict, options: dict) -> str:
    await _post(cfg, "messages.json", {"title": video["title"][:250], "message": video["url"], "url": video["url"], "url_title": "Open video"})
    return "Sent to Pushover."


async def notify(cfg: dict, title: str, message: str, link: str) -> None:
    await _post(cfg, "messages.json", {"title": title[:250], "message": message[:1000] or title, **({"url": link, "url_title": "Open search"} if link else {})})
