"""MeTube (https://github.com/alexta69/metube): POST <url>/add, same choices as its own form."""
from __future__ import annotations

from ..net import NetError, as_json, clean_url, guarded_request

KIND = "metube"
LABEL = "MeTube"
DESCRIPTION = "Downloads the video on your MeTube server, with MeTube's usual Type, Codec, Format and Quality choices."
FIELDS = [
    {"name": "url", "label": "MeTube address", "type": "url", "required": True, "placeholder": "https://metube.example.com"},
    {"name": "username", "label": "Username (if your MeTube has a login)", "type": "text"},
    {"name": "password", "label": "Password", "type": "password"},
]
OPTIONS = "metube"

# Same rules as MeTube's own form (app/main.py: parse_download_options).
VIDEO_QUALITIES = {"best", "2160", "1440", "1080", "720", "480", "360", "240", "worst"}
AUDIO_QUALITIES = {"mp3": {"best", "320", "192", "128"}, "m4a": {"best", "192", "128"},
                   "opus": {"best"}, "wav": {"best"}, "flac": {"best"}}
FORMATS = {
    "video": {"any", "mp4", "ios"},
    "audio": set(AUDIO_QUALITIES),
    "captions": {"srt", "txt", "vtt", "ttml", "sbv", "scc", "dfxp"},
    "thumbnail": {"jpg"},
}
CODECS = {"auto", "h264", "h265", "av1", "vp9"}


def _base(cfg: dict) -> str:
    return clean_url(cfg["url"], example="https://metube.example.com").rstrip("/") + "/"


def _auth(cfg: dict):
    return (cfg["username"], cfg.get("password") or "") if cfg.get("username") else None


async def check(cfg: dict) -> str:
    cfg["url"] = _base(cfg)
    r = await guarded_request("GET", cfg["url"] + "version", auth=_auth(cfg), what="MeTube")
    info = as_json(r) if r.status_code == 200 else None
    if not info or "version" not in info:
        raise NetError(f"That address answered (HTTP {r.status_code}), but not like MeTube does. "
                       "Check the address, including any path MeTube is served under.")
    return f"MeTube {str(info.get('version'))[:40]}"


def _options(o: dict) -> dict:
    kind = str(o.get("download_type") or "video")
    fmt = str(o.get("format") or "any")
    codec = str(o.get("codec") or "auto")
    quality = str(o.get("quality") or "best")
    if kind not in FORMATS or fmt not in FORMATS[kind]:
        raise NetError("Pick a type and format.")
    if kind == "video":
        if codec not in CODECS or quality not in VIDEO_QUALITIES:
            raise NetError("Pick a codec and quality.")
    elif kind == "audio":
        if quality not in AUDIO_QUALITIES[fmt]:
            raise NetError("Pick a quality.")
        codec = "auto"
    else:
        codec, quality = "auto", "best"
    return {"download_type": kind, "codec": codec, "format": fmt, "quality": quality}


async def send(cfg: dict, video: dict, options: dict) -> str:
    payload = {"url": video["url"], **_options(options), "auto_start": True}
    # MeTube looks the video up before answering, which can take a while.
    r = await guarded_request("POST", _base(cfg) + "add", json_body=payload, auth=_auth(cfg), timeout=90, what="MeTube")
    data = as_json(r) or {}
    if r.status_code != 200:
        reason = (r.reason or "").strip() if r.status_code == 400 else ""
        raise NetError(f"MeTube didn't accept it (HTTP {r.status_code}{': ' + reason[:200] if reason else ''}).")
    if data.get("status") != "ok":
        raise NetError("MeTube couldn't add it: " + str(data.get("msg") or "unknown error")[:300])
    return str(data.get("msg") or "Sent to MeTube. It's downloading there now.")[:300]


async def status(cfg: dict, urls: list[str]) -> dict:
    """Where each URL is in MeTube: queued / downloading / finished / error."""
    r = await guarded_request("GET", _base(cfg) + "history", auth=_auth(cfg), timeout=20, what="MeTube")
    data = as_json(r) or {}
    wanted, out = set(urls), {}
    # Active entries first, so a video sent again shows its new state, not the old result.
    for where in ("queue", "pending", "done"):
        for item in data.get(where) or []:
            u = item.get("url")
            if u not in wanted or u in out:
                continue
            if where == "done":
                st = "finished" if item.get("status") == "finished" else "error"
            else:
                st = "downloading" if where == "queue" and item.get("status") == "downloading" else "queued"
            pct = item.get("percent")
            ts = item.get("timestamp")  # nanoseconds, when MeTube got it
            out[u] = {"status": st, "msg": str(item.get("msg") or "")[:200],
                      "percent": round(pct) if isinstance(pct, (int, float)) else None,
                      "added": ts / 1e9 if isinstance(ts, (int, float)) else None}
    return out
