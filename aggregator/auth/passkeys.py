"""WebAuthn: passkeys and security keys (py_webauthn).

- passkey: discoverable credential with user verification. Signs in on its own,
  no email or password needed (it is already two factors: device + PIN/biometric).
- security_key: roaming authenticator (USB/NFC key). Signs in after entering the
  email when the key verifies the user (PIN); otherwise it serves as the second
  step after a password.
"""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import HTTPException, Request
from webauthn import (
    generate_authentication_options, generate_registration_options, options_to_json,
    verify_authentication_response, verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorAttachment, AuthenticatorSelectionCriteria, AuthenticatorTransport,
    PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement,
)

from . import db
from .config import settings
from .sessions import is_https

RP_NAME = "Naughty Site Aggregator"


def rp(request: Request) -> tuple[str, str]:
    """Relying party id and expected origin. On the public host these come from
    PUBLIC_URL; on localhost (development) from the request itself."""
    host = (request.headers.get("host") or "").split(":")[0]
    if settings.public_host and host == settings.public_host:
        return settings.public_host, settings.public_origin
    if host in ("localhost", "127.0.0.1"):
        return "localhost", f"{request.url.scheme}://{request.headers.get('host')}"
    if not is_https(request):
        raise HTTPException(400, "Passkeys and security keys need the HTTPS address of this site.")
    return host, f"https://{request.headers.get('host')}"


def _descriptors(rows) -> list[PublicKeyCredentialDescriptor]:
    out = []
    for r in rows:
        transports = []
        for t in r["transports"] or []:
            try:
                transports.append(AuthenticatorTransport(t))
            except ValueError:
                pass
        out.append(PublicKeyCredentialDescriptor(id=bytes(r["credential_id"]), transports=transports or None))
    return out


async def registration_options(request: Request, user, kind: str, display_name: str) -> tuple[dict, str]:
    rp_id, _ = rp(request)
    async with db.pool.acquire() as c:
        existing = await c.fetch("SELECT credential_id, transports FROM webauthn_credentials WHERE user_id = $1", user["id"])
    if kind == "passkey":
        selection = AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED)
    else:
        selection = AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.CROSS_PLATFORM,
            resident_key=ResidentKeyRequirement.DISCOURAGED, user_verification=UserVerificationRequirement.PREFERRED)
    opts = generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME, user_id=bytes(user["webauthn_handle"]),
        user_name=display_name, user_display_name=display_name,
        authenticator_selection=selection, exclude_credentials=_descriptors(existing),
    )
    return json.loads(options_to_json(opts)), bytes_to_base64url(opts.challenge)


async def verify_registration(request: Request, user_id: UUID, credential: dict, challenge: str, kind: str) -> dict:
    rp_id, origin = rp(request)
    try:
        v = verify_registration_response(
            credential=credential, expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=rp_id, expected_origin=origin, require_user_verification=(kind == "passkey"),
        )
    except Exception as e:
        raise HTTPException(400, f"That key couldn't be registered: {e}")
    transports = (credential.get("response") or {}).get("transports") or []
    return {
        "credential_id": v.credential_id, "public_key": v.credential_public_key, "sign_count": v.sign_count,
        "backed_up": bool(v.credential_backed_up), "transports": [str(t) for t in transports][:8],
    }


async def authentication_options(request: Request, allow_rows=None, require_uv: bool = True) -> tuple[dict, str]:
    rp_id, _ = rp(request)
    opts = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=_descriptors(allow_rows) if allow_rows is not None else None,
        user_verification=UserVerificationRequirement.REQUIRED if require_uv else UserVerificationRequirement.PREFERRED,
    )
    return json.loads(options_to_json(opts)), bytes_to_base64url(opts.challenge)


async def verify_authentication(request: Request, credential: dict, challenge: str, require_uv: bool,
                                user_id: UUID | None = None):
    """Checks the assertion; returns the stored credential row (with user_id)."""
    rp_id, origin = rp(request)
    try:
        cred_id = base64url_to_bytes(credential.get("rawId") or credential.get("id") or "")
    except Exception:
        raise HTTPException(400, "Unrecognised key.")
    async with db.pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM webauthn_credentials WHERE credential_id = $1", cred_id)
    if not row or (user_id is not None and row["user_id"] != user_id):
        raise HTTPException(400, "That passkey or key isn't registered here.")
    try:
        v = verify_authentication_response(
            credential=credential, expected_challenge=base64url_to_bytes(challenge), expected_rp_id=rp_id,
            expected_origin=origin, credential_public_key=bytes(row["public_key"]),
            credential_current_sign_count=row["sign_count"], require_user_verification=require_uv,
        )
    except Exception:
        raise HTTPException(400, "The passkey or key check failed. Try again.")
    async with db.pool.acquire() as c:
        await c.execute("UPDATE webauthn_credentials SET sign_count = $2, last_used_at = now(), backed_up = $3 WHERE id = $1",
                        row["id"], v.new_sign_count, bool(v.credential_backed_up))
    return row, bool(v.user_verified)
