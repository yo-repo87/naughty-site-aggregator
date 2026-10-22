"""Outgoing requests to addresses that users typed in (MeTube, webhooks, ntfy...).

Anyone who can register can make the server contact such an address, so every
request goes through guarded_request():
- http/https only, no credentials inside the URL, redirects never followed;
- the host must resolve to public IPs only, unless the admin lists it in
  SEND_PRIVATE_HOSTS ("host", "host:port" or a CIDR; METUBE_PRIVATE_HOSTS is
  the older name and still read);
- the connection is pinned to the address that was checked (CurlOpt.RESOLVE),
  so DNS can't be switched between the check and the request.
Errors come back as NetError with a message that is safe to show the user.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
from urllib.parse import urlsplit

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession

PRIVATE_HOSTS = tuple(
    h.strip().lower()
    for var in ("SEND_PRIVATE_HOSTS", "METUBE_PRIVATE_HOSTS")
    for h in os.environ.get(var, "").split(",") if h.strip()
)


class NetError(Exception):
    """Message is shown to the user as is."""


def clean_url(raw: str, *, example: str = "https://example.com", keep_query: bool = False) -> str:
    raw = (raw or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
    u = urlsplit(raw)
    if u.scheme not in ("http", "https") or not u.hostname or len(raw) > 500:
        raise NetError(f"Enter a full address, e.g. {example}")
    if u.username or u.password:
        raise NetError("Put the username and password in their own boxes, not in the address.")
    if u.fragment or (u.query and not keep_query):
        raise NetError("Leave out anything after ? or # in the address.")
    try:
        u.port
    except ValueError:
        raise NetError("That address has an invalid port.") from None
    query = f"?{u.query}" if u.query else ""
    return f"{u.scheme}://{u.netloc.lower()}{u.path}{query}"


def origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}"


def _listed(host: str, ip, port: int) -> bool:
    for entry in PRIVATE_HOSTS:
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
        raise NetError("Couldn't find that address (DNS lookup failed).") from None
    ips = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    for ip in ips:
        if not ip.is_global and not _listed(host, ip, port):
            raise NetError("That address is on a private network, which this server isn't allowed to reach. "
                           "Use its public address, or ask the admin to add it to SEND_PRIVATE_HOSTS.")
    return str(ips[0])


async def guarded_request(method: str, url: str, *, json_body=None, data=None, headers=None,
                          auth: tuple[str, str] | None = None, timeout: float = 15, what: str = "the service"):
    u = urlsplit(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    ip = await _pinned_address(u.hostname, port)
    pin = f"[{ip}]" if ":" in ip else ip
    opts = {CurlOpt.RESOLVE: [f"{u.hostname}:{port}:{pin}"]}
    try:
        async with AsyncSession(timeout=timeout, allow_redirects=False, trust_env=False, curl_options=opts) as s:
            r = await s.request(method, url, json=json_body, data=data, auth=auth, headers=headers or {})
    except Exception:
        raise NetError(f"Couldn't reach {what} at that address.") from None
    if r.status_code in (401, 403):
        raise NetError(f"{what[0].upper() + what[1:]} refused the login. Check the username, password or token.")
    if 300 <= r.status_code < 400:
        raise NetError(f"The address of {what} redirected somewhere else. Use the address it finally opens at.")
    return r


def as_json(r) -> dict | None:
    try:
        data = json.loads(r.content[:262144])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
