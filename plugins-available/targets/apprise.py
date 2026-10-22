"""Apprise API — optional send-to / alert plug-in: one connection to 100+ services
(email, Matrix, Slack, Signal, Pushbullet, SMS…) through your Apprise API server."""
from __future__ import annotations

import re

from aggregator.net import NetError, clean_url, guarded_request

KIND = "apprise"
LABEL = "Apprise"
DESCRIPTION = ("Notifies through your Apprise API server (github.com/caronc/apprise-api), which reaches email, Matrix, "
               "Slack, Signal, SMS and 100+ other services. Use a saved configuration key, or paste Apprise URLs.")
FIELDS = [
    {"name": "url", "label": "Apprise API address", "type": "url", "required": True, "placeholder": "http://apprise.example.com:8000"},
    {"name": "key", "label": "Configuration key (optional)", "type": "text", "placeholder": "apprise",
     "help": "Uses the configuration saved under this key on the server. Leave empty to use the URLs below."},
    {"name": "urls", "label": "Apprise URLs (if no key)", "type": "password", "placeholder": "mailto://user:pass@gmail.com, tgram://…"},
    {"name": "tag", "label": "Tag (optional)", "type": "text", "placeholder": "all"},
]
OPTIONS = []
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


async def _post(cfg: dict, title: str, body: str) -> None:
    base = clean_url(cfg["url"], example="http://apprise.example.com:8000").rstrip("/")
    key = (cfg.get("key") or "").strip()
    payload = {"title": title[:250], "body": body[:3000] or title, "type": "info", "format": "text"}
    if (cfg.get("tag") or "").strip():
        payload["tag"] = cfg["tag"].strip()[:100]
    if key:
        if not KEY_RE.match(key):
            raise NetError("The configuration key can only contain letters, digits, - and _.")
        url = f"{base}/notify/{key}"
    elif (cfg.get("urls") or "").strip():
        url, payload["urls"] = f"{base}/notify/", cfg["urls"].strip()
    else:
        raise NetError("Give a configuration key or Apprise URLs.")
    r = await guarded_request("POST", url, json_body=payload, what="Apprise", timeout=30)
    if not 200 <= r.status_code < 300:
        raise NetError(f"Apprise answered HTTP {r.status_code}" + (" (nothing matched that key or tag)" if r.status_code in (204, 404, 424) else "") + ".")


async def check(cfg: dict) -> str:
    cfg["url"] = clean_url(cfg["url"]).rstrip("/")
    await _post(cfg, "Aggregator connected", "Notifications from Aggregator will arrive here.")
    return "Test notification sent"


async def send(cfg: dict, video: dict, options: dict) -> str:
    await _post(cfg, video["title"], video["url"])
    return "Sent through Apprise."


async def notify(cfg: dict, title: str, message: str, link: str) -> None:
    await _post(cfg, title, f"{message}\n{link}".strip())
