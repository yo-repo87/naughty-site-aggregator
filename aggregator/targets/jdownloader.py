"""JDownloader 2 via its Click'n'Load listener (port 9666), which takes links over plain HTTP.

Click'n'Load only listens on the machine running JDownloader, so this needs that
address listed in SEND_PRIVATE_HOSTS (or JDownloader published through a proxy).
"""
from __future__ import annotations

from ..net import NetError, clean_url, guarded_request

KIND = "jdownloader"
LABEL = "JDownloader"
DESCRIPTION = "Adds the video to JDownloader 2's link grabber through Click'n'Load (port 9666)."
FIELDS = [
    {"name": "url", "label": "Click'n'Load address", "type": "url", "required": True, "placeholder": "http://192.168.1.20:9666",
     "help": "The computer running JDownloader, port 9666. A LAN address must be allowed by the admin (SEND_PRIVATE_HOSTS)."},
    {"name": "package", "label": "Package name (optional)", "type": "text", "placeholder": "Aggregator"},
]
OPTIONS = []


def _base(cfg: dict) -> str:
    return clean_url(cfg["url"], example="http://192.168.1.20:9666").rstrip("/")


async def check(cfg: dict) -> str:
    cfg["url"] = _base(cfg)
    r = await guarded_request("GET", cfg["url"] + "/jdcheck.js", what="JDownloader")
    if r.status_code != 200 or b"jdownloader" not in r.content[:2000].lower():
        raise NetError(f"That address answered (HTTP {r.status_code}), but not like JDownloader's Click'n'Load does.")
    return "JDownloader is listening"


async def send(cfg: dict, video: dict, options: dict) -> str:
    form = {"urls": video["url"], "source": "Aggregator", "package": (cfg.get("package") or video["title"])[:120]}
    r = await guarded_request("POST", _base(cfg) + "/flash/add", data=form, what="JDownloader")
    if r.status_code != 200 or b"success" not in r.content[:200].lower():
        raise NetError(f"JDownloader didn't accept it (HTTP {r.status_code}).")
    return "Added to JDownloader's link grabber."
