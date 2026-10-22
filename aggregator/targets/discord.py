"""Discord channel webhook: posts the video link (Discord shows its own preview)."""
from __future__ import annotations

from urllib.parse import urlsplit

from ..net import NetError, as_json, clean_url, guarded_request

KIND = "discord"
LABEL = "Discord"
DESCRIPTION = "Posts the video link to a Discord channel through a channel webhook (Channel settings → Integrations → Webhooks)."
FIELDS = [
    {"name": "url", "label": "Webhook URL", "type": "password", "required": True,
     "placeholder": "https://discord.com/api/webhooks/…", "help": "Kept secret like a password: anyone with it can post to the channel."},
]
OPTIONS = []


def _url(cfg: dict) -> str:
    url = clean_url(cfg.get("url") or "", example="https://discord.com/api/webhooks/…")
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname not in ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com") \
            or not u.path.startswith("/api/webhooks/"):
        raise NetError("Use the webhook URL Discord gives you (https://discord.com/api/webhooks/…).")
    return url


async def _post(cfg: dict, content: str) -> None:
    r = await guarded_request("POST", _url(cfg), json_body={"content": content[:1900], "username": "Aggregator"}, what="Discord")
    if not 200 <= r.status_code < 300:
        raise NetError(f"Discord answered HTTP {r.status_code}.")


async def check(cfg: dict) -> str:
    cfg["url"] = _url(cfg)
    r = await guarded_request("GET", cfg["url"], what="Discord")
    info = as_json(r) if r.status_code == 200 else None
    if not info or "id" not in info:
        raise NetError("Discord doesn't know that webhook (deleted, or the URL is incomplete).")
    return f"Discord webhook “{str(info.get('name') or 'webhook')[:60]}”"


async def send(cfg: dict, video: dict, options: dict) -> str:
    await _post(cfg, f"**{video['title']}**\n{video['url']}")
    return "Posted to Discord."


async def notify(cfg: dict, title: str, message: str, link: str) -> None:
    await _post(cfg, f"**{title}**\n{message}\n{link}".strip())
