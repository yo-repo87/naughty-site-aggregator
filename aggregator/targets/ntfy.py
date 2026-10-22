"""ntfy (https://ntfy.sh or self-hosted): push notifications to your phone."""
from __future__ import annotations

import re

from ..net import NetError, clean_url, guarded_request

KIND = "ntfy"
LABEL = "ntfy"
DESCRIPTION = "Sends a push notification through ntfy (ntfy.sh or your own server). Tapping it opens the video or the search."
FIELDS = [
    {"name": "server", "label": "Server", "type": "url", "required": True, "placeholder": "https://ntfy.sh"},
    {"name": "topic", "label": "Topic", "type": "text", "required": True, "placeholder": "my-secret-topic",
     "help": "On ntfy.sh anyone who knows the topic can read it: pick something hard to guess."},
    {"name": "token", "label": "Access token (optional)", "type": "password", "placeholder": "tk_…"},
]
OPTIONS = []
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _url(cfg: dict) -> str:
    server = clean_url(cfg.get("server") or "https://ntfy.sh", example="https://ntfy.sh").rstrip("/")
    topic = (cfg.get("topic") or "").strip()
    if not TOPIC_RE.match(topic):
        raise NetError("The topic can only contain letters, digits, - and _ (up to 64).")
    return f"{server}/{topic}"


def _header(text: str) -> str:
    # HTTP headers are Latin-1: drop what doesn't fit rather than fail.
    return text.encode("latin-1", "ignore").decode("latin-1").replace("\n", " ")[:250]


async def _post(cfg: dict, title: str, message: str, link: str) -> None:
    headers = {"Title": _header(title), "Tags": "movie_camera"}
    if link:
        headers["Click"] = link
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    r = await guarded_request("POST", _url(cfg), data=message.encode()[:3500], headers=headers, what="ntfy")
    if not 200 <= r.status_code < 300:
        raise NetError(f"ntfy answered HTTP {r.status_code}.")


async def check(cfg: dict) -> str:
    cfg["server"] = clean_url(cfg.get("server") or "https://ntfy.sh").rstrip("/")
    await _post(cfg, "Aggregator connected", "Notifications from Aggregator will arrive here.", "")
    return "Test notification sent"


async def send(cfg: dict, video: dict, options: dict) -> str:
    await _post(cfg, video["title"], video["url"], video["url"])
    return "Sent to ntfy."


async def notify(cfg: dict, title: str, message: str, link: str) -> None:
    await _post(cfg, title, message, link)
