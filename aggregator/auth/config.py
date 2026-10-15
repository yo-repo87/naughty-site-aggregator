"""Account settings, read once from the environment.

Accounts are entirely optional: without DATABASE_URL, DATA_KEY and INDEX_KEY the
app runs exactly as before and the UI shows no sign-in. Each OAuth provider is
enabled only when both its client id and secret are set.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    return default if not v else v in ("1", "true", "yes", "on")


def _key(name: str) -> bytes | None:
    v = os.environ.get(name, "").strip()
    if not v:
        return None
    raw = base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
    if len(raw) != 32:
        raise RuntimeError(f"{name} must be 32 bytes, base64url-encoded")
    return raw


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str


@dataclass
class Settings:
    database_url: str = ""
    db_ssl: str = "prefer"
    db_ssl_root_cert: str = ""
    data_key: bytes | None = None
    index_key: bytes | None = None
    public_url: str = ""
    require_https: bool = False
    require_login: bool = False
    allow_registration: bool = True
    providers: dict[str, Provider] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.database_url and self.data_key and self.index_key)

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).hostname or ""

    @property
    def public_origin(self) -> str:
        u = urlparse(self.public_url)
        return f"{u.scheme}://{u.netloc}" if u.scheme else ""


_PROVIDER_DEFS = {
    "google": dict(
        label="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email profile",
    ),
    "facebook": dict(
        label="Facebook",
        authorize_url="https://www.facebook.com/v21.0/dialog/oauth",
        token_url="https://graph.facebook.com/v21.0/oauth/access_token",
        userinfo_url="https://graph.facebook.com/v21.0/me?fields=id,name,email",
        scope="email public_profile",
    ),
    "discord": dict(
        label="Discord",
        authorize_url="https://discord.com/oauth2/authorize",
        token_url="https://discord.com/api/oauth2/token",
        userinfo_url="https://discord.com/api/users/@me",
        scope="identify email",
    ),
}


def load() -> Settings:
    providers = {}
    for name, d in _PROVIDER_DEFS.items():
        cid = os.environ.get(f"{name.upper()}_CLIENT_ID", "").strip()
        secret = os.environ.get(f"{name.upper()}_CLIENT_SECRET", "").strip()
        if cid and secret:
            providers[name] = Provider(name=name, client_id=cid, client_secret=secret, **d)
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "").strip(),
        db_ssl=os.environ.get("DB_SSL", "prefer").strip() or "prefer",
        db_ssl_root_cert=os.environ.get("DB_SSL_ROOT_CERT", "").strip(),
        data_key=_key("DATA_KEY"),
        index_key=_key("INDEX_KEY"),
        public_url=os.environ.get("PUBLIC_URL", "").strip().rstrip("/"),
        require_https=_bool("REQUIRE_HTTPS", False),
        require_login=_bool("REQUIRE_LOGIN", False),
        allow_registration=_bool("ALLOW_REGISTRATION", True),
        providers=providers,
    )


settings = load()
