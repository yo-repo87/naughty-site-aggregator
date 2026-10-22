"""'Send to' targets: places a video (or an alert) can be sent.

Each target is a module (built in here, or a plug-in in PLUGINS_DIR/targets/)
with:
    KIND, LABEL, DESCRIPTION: str
    FIELDS: list of {name, label, type: url|text|password, required, placeholder, help}
            ("password" fields are secrets: stored encrypted, never sent back to the browser)
    OPTIONS: list of {name, label, choices: [[id, text], ...], default} asked for on each send,
             or the string "metube" for MeTube's own Type/Codec/Format/Quality form
    async def check(cfg) -> str         validate + test on save; returns a short status line
    async def send(cfg, video, options) -> str     (optional) video: url, title, source, duration, thumb
    async def notify(cfg, title, message, link) -> None   (optional) used by saved-search alerts
    async def status(cfg, urls) -> {url: {"status", "msg"}}  (optional) download progress
Raise TargetError (or net.NetError) with a message for the user.
"""
from __future__ import annotations

from ..net import NetError
from . import discord, jdownloader, metube, ntfy, webhook


class TargetError(Exception):
    """Message is shown to the user as is."""


Errors = (TargetError, NetError)

TARGETS = {m.KIND: m for m in (metube, jdownloader, webhook, discord, ntfy)}


def describe(m) -> dict:
    return {
        "kind": m.KIND, "label": m.LABEL, "description": getattr(m, "DESCRIPTION", ""),
        "fields": getattr(m, "FIELDS", []), "options": getattr(m, "OPTIONS", []),
        "can_send": hasattr(m, "send"), "can_notify": hasattr(m, "notify"), "has_status": hasattr(m, "status"),
    }


def secret_fields(m) -> set[str]:
    return {f["name"] for f in getattr(m, "FIELDS", []) if f.get("type") == "password"}


def url_fields(m) -> set[str]:
    return {f["name"] for f in getattr(m, "FIELDS", []) if f.get("type") == "url"}
