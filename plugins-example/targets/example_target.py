"""Template for a "Send to" plug-in. Copy to plugins/targets/<name>.py.

Users connect it in Account → Profile → Send to & notifications. The fields below become the
form; "password" fields are stored encrypted and never shown again. Always use
guarded_request() for network calls: it refuses private-network addresses (unless the admin
allows them in SEND_PRIVATE_HOSTS) and never follows redirects.
"""
from __future__ import annotations

from aggregator.net import NetError, clean_url, guarded_request

KIND = "exampletarget"          # unique id
LABEL = "Example service"
DESCRIPTION = "Sends the video link to Example service."
FIELDS = [
    {"name": "url", "label": "Server address", "type": "url", "required": True, "placeholder": "https://example.com"},
    {"name": "api_key", "label": "API key", "type": "password", "required": True},
]
# Asked on every send (optional): each a list of [id, label] choices.
OPTIONS = [
    {"name": "folder", "label": "Folder", "choices": [["default", "Default"], ["later", "Watch later"]], "default": "default"},
]


async def check(cfg: dict) -> str:
    """Runs when the user saves: test the connection, raise NetError("message") if it's wrong."""
    cfg["url"] = clean_url(cfg["url"]).rstrip("/")
    r = await guarded_request("GET", cfg["url"] + "/api/ping", headers={"X-Api-Key": cfg["api_key"]}, what="Example service")
    if r.status_code != 200:
        raise NetError(f"Example service answered HTTP {r.status_code}.")
    return "Connected"


async def send(cfg: dict, video: dict, options: dict) -> str:
    """video: url, title, source, duration, thumb. options: the OPTIONS choices. Returns a message."""
    r = await guarded_request("POST", cfg["url"] + "/api/add", headers={"X-Api-Key": cfg["api_key"]},
                              json_body={"url": video["url"], "title": video["title"], "folder": options.get("folder")},
                              what="Example service")
    if r.status_code != 200:
        raise NetError(f"Example service didn't accept it (HTTP {r.status_code}).")
    return "Sent to Example service."


# Optional: lets saved-search alerts notify through this target.
# async def notify(cfg: dict, title: str, message: str, link: str) -> None: ...
