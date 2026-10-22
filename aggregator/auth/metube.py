"""Per-user MeTube: each account can save its own MeTube address (plus an optional
basic-auth login) and send videos there. The server relays the request, so a
MeTube on plain HTTP or behind a login works from any device.

The relay is a request the server makes on a user's behalf, so it is fenced in:
- only POST <MeTube>/add (and GET <MeTube>/version to check the address), with
  redirects off and nothing but a short status passed back;
- the address must resolve to public IPs, unless the admin lists a LAN host in
  METUBE_PRIVATE_HOSTS; the connection is pinned to the address that was
  checked, so DNS can't be switched in between;
- only video pages of the supported sites can be sent.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from fastapi import APIRouter, Body, HTTPException, Request

from . import crypto, db
from .config import settings
from .sessions import rate_limit, require_session

router = APIRouter()

# Video pages that may be sent: the twelve supported sites.
SITE_DOMAINS = ("pornhub.com", "xvideos.com", "spankbang.com", "tube8.com", "youporn.com", "redtube.com",
                "eporner.com", "xnxx.com", "xhamster.com", "youjizz.com", "porntrex.com", "hqporner.com")
# Same choices and rules as MeTube's own form (app/main.py: parse_download_options).
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


def _where(uid) -> str:
    return f"users.metube:{uid}"


async def _load(uid) -> dict | None:
    async with db.pool.acquire() as c:
        blob = await c.fetchval("SELECT metube_enc FROM users WHERE id = $1", uid)
    return crypto.open_json(blob, _where(uid))


async def configured(uid) -> bool:
    async with db.pool.acquire() as c:
        return await c.fetchval("SELECT metube_enc IS NOT NULL FROM users WHERE id = $1", uid)


def _enabled() -> None:
    if not settings.enabled or not db.pool:
        raise HTTPException(404, "Accounts are not enabled on this server.")


def _clean_url(raw: str) -> str:
    raw = raw.strip()
    if "://" not in raw:
        raw = "http://" + raw
    u = urlsplit(raw)
    if u.scheme not in ("http", "https") or not u.hostname or len(raw) > 300:
        raise HTTPException(400, "Enter MeTube's address, e.g. https://metube.example.com")
    if u.username or u.password:
        raise HTTPException(400, "Put the username and password in their own boxes, not in the address.")
    if u.query or u.fragment:
        raise HTTPException(400, "Leave out anything after ? or # in the address.")
    try:
        u.port
    except ValueError:
        raise HTTPException(400, "That address has an invalid port.") from None
    return f"{u.scheme}://{u.netloc.lower()}{u.path.rstrip('/')}/"


def _origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}"


def _listed(host: str, ip, port: int) -> bool:
    for entry in settings.metube_private_hosts:
        if "/" in entry:
            try:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                pass
            continue
        name, _, p = entry.rpartition(":") if entry.count(":") == 1 else (entry, "", "")
        if p and p != str(port):
            continue
        if name in (host, str(ip)):
            return True
    return False


async def _pinned_address(host: str, port: int) -> str:
    """Resolve once, check every address, and return the one to connect to."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise HTTPException(502, "Couldn't find that MeTube address (DNS lookup failed).") from None
    ips = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    for ip in ips:
        if not ip.is_global and not _listed(host, ip, port):
            raise HTTPException(400, "That address is on a private network, which this server isn't allowed to "
                                     "reach. Use MeTube's public address, or ask the admin to add it to METUBE_PRIVATE_HOSTS.")
    return str(ips[0])


async def _call(cfg: dict, method: str, path: str, payload: dict | None = None, timeout: float = 15):
    u = urlsplit(cfg["url"])
    port = u.port or (443 if u.scheme == "https" else 80)
    ip = await _pinned_address(u.hostname, port)
    pin = f"[{ip}]" if ":" in ip else ip
    opts = {CurlOpt.RESOLVE: [f"{u.hostname}:{port}:{pin}"]}
    auth = (cfg["username"], cfg.get("password") or "") if cfg.get("username") else None
    try:
        async with AsyncSession(timeout=timeout, allow_redirects=False, trust_env=False, curl_options=opts) as s:
            r = await s.request(method, cfg["url"] + path, json=payload, auth=auth, headers={"Accept": "application/json"})
    except Exception:
        raise HTTPException(502, "Couldn't reach MeTube at that address.") from None
    if r.status_code in (401, 403):
        raise HTTPException(502, "MeTube refused the login. Check the username and password.")
    if 300 <= r.status_code < 400:
        raise HTTPException(502, "MeTube's address redirected somewhere else. Use the address its page finally opens at.")
    return r


def _json(r) -> dict | None:
    try:
        data = json.loads(r.content[:65536])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ======================= settings (Profile tab) =======================

@router.get("/api/me/metube")
async def get_metube(request: Request):
    _enabled()
    s = await require_session(request)
    cfg = await _load(s["user_id"])
    if not cfg:
        return {"configured": False}
    # The password never goes back to the browser.
    return {"configured": True, "url": cfg["url"], "username": cfg.get("username") or "",
            "has_password": bool(cfg.get("password"))}


@router.put("/api/me/metube")
async def put_metube(request: Request, body: dict = Body(...)):
    """Checks the address and login against MeTube's /version, then saves."""
    _enabled()
    s = await require_session(request)
    uid = s["user_id"]
    rate_limit(request, "metube-save", 10, 60)
    url = _clean_url(str(body.get("url") or ""))
    username = str(body.get("username") or "").strip()[:200]
    password = str(body.get("password") or "")[:500]
    old = await _load(uid)
    # Keep the saved password only for the same MeTube: otherwise anyone at an
    # unlocked screen could re-point the address and collect it.
    if not password and username and old and old.get("password") and old.get("username") == username \
            and _origin(old["url"]) == _origin(url):
        password = old["password"]
    if not username:
        password = ""
    cfg = {"url": url, "username": username, "password": password}

    r = await _call(cfg, "GET", "version")
    info = _json(r) if r.status_code == 200 else None
    if not info or "version" not in info:
        raise HTTPException(502, f"That address answered (HTTP {r.status_code}), but not like MeTube does. "
                                 "Check the address, including any path MeTube is served under.")
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET metube_enc = $2 WHERE id = $1", uid, crypto.seal_json(cfg, _where(uid)))
    return {"ok": True, "version": str(info.get("version"))[:40]}


@router.delete("/api/me/metube")
async def delete_metube(request: Request):
    _enabled()
    s = await require_session(request)
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET metube_enc = NULL WHERE id = $1", s["user_id"])
    return {"ok": True}


# ======================= sending a video =======================

def _check_options(body: dict) -> dict:
    kind = str(body.get("download_type") or "video")
    fmt = str(body.get("format") or "")
    codec = str(body.get("codec") or "auto")
    quality = str(body.get("quality") or "best")
    if kind not in FORMATS or fmt not in FORMATS[kind]:
        raise HTTPException(400, "Pick a type and format.")
    if kind == "video":
        if codec not in CODECS or quality not in VIDEO_QUALITIES:
            raise HTTPException(400, "Pick a codec and quality.")
    elif kind == "audio":
        if quality not in AUDIO_QUALITIES[fmt]:
            raise HTTPException(400, "Pick a quality.")
        codec = "auto"
    else:
        codec, quality = "auto", "best"
    return {"download_type": kind, "codec": codec, "format": fmt, "quality": quality}


def _check_video_url(raw: str) -> str:
    u = urlsplit(raw.strip())
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not any(host == d or host.endswith("." + d) for d in SITE_DOMAINS) or len(raw) > 1000:
        raise HTTPException(400, "Only videos from the supported sites can be sent to MeTube.")
    return raw.strip()


@router.post("/api/metube/add")
async def add(request: Request, body: dict = Body(...)):
    _enabled()
    s = await require_session(request)
    rate_limit(request, "metube-add", 30, 60)
    cfg = await _load(s["user_id"])
    if not cfg:
        raise HTTPException(400, "Set up MeTube in your account's Profile tab first.")
    payload = {"url": _check_video_url(str(body.get("url") or "")), **_check_options(body), "auto_start": True}
    # MeTube looks the video up before answering, which can take a while.
    r = await _call(cfg, "POST", "add", payload, timeout=90)
    data = _json(r) or {}
    if r.status_code != 200:
        reason = (r.reason or "").strip() if r.status_code == 400 else ""
        raise HTTPException(502, f"MeTube didn't accept it (HTTP {r.status_code}{': ' + reason[:200] if reason else ''}).")
    if data.get("status") != "ok":
        raise HTTPException(502, "MeTube couldn't add it: " + str(data.get("msg") or "unknown error")[:300])
    return {"ok": True, "message": str(data.get("msg") or "")[:300]}
