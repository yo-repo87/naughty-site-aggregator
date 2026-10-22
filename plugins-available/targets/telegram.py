"""Telegram — optional send-to / alert plug-in: a bot posts the video link to your chat."""
from __future__ import annotations

import re

from aggregator.net import NetError, as_json, guarded_request

KIND = "telegram"
LABEL = "Telegram"
DESCRIPTION = ("A Telegram bot posts the video link (or the alert) to your chat. Create a bot with @BotFather, "
               "send it a message, then use your chat id (e.g. from @userinfobot).")
FIELDS = [
    {"name": "token", "label": "Bot token", "type": "password", "required": True, "placeholder": "123456:ABC-…"},
    {"name": "chat_id", "label": "Chat id", "type": "text", "required": True, "placeholder": "123456789 or @channelname"},
]
OPTIONS = []
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
CHAT_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z0-9_]{5,32})$")


async def _call(cfg: dict, method: str, body: dict) -> dict:
    token, chat = (cfg.get("token") or "").strip(), (cfg.get("chat_id") or "").strip()
    if not TOKEN_RE.match(token):
        raise NetError("That doesn't look like a bot token (it comes from @BotFather, like 123456:ABC-…).")
    if not CHAT_RE.match(chat):
        raise NetError("The chat id is a number (e.g. 123456789, or -100… for groups) or @channelname.")
    r = await guarded_request("POST", f"https://api.telegram.org/bot{token}/{method}", json_body={"chat_id": chat, **body}, what="Telegram")
    data = as_json(r) or {}
    if not data.get("ok"):
        raise NetError(f"Telegram said: {str(data.get('description') or r.status_code)[:200]}")
    return data.get("result") or {}


async def check(cfg: dict) -> str:
    chat = await _call(cfg, "getChat", {})
    return f"Telegram chat “{str(chat.get('title') or chat.get('first_name') or chat.get('username') or 'chat')[:60]}”"


async def send(cfg: dict, video: dict, options: dict) -> str:
    await _call(cfg, "sendMessage", {"text": f"{video['title']}\n{video['url']}"[:4000]})
    return "Posted to Telegram."


async def notify(cfg: dict, title: str, message: str, link: str) -> None:
    await _call(cfg, "sendMessage", {"text": f"{title}\n{message}\n{link}".strip()[:4000], "disable_web_page_preview": True})
