"""Kino's gate token (signed by @kinocodigo_bot) and this server's own sessions.

Token: base64url(JSON payload) + "." + base64url(Ed25519 signature of the first part's ASCII bytes),
payload {app, device, iat, tg, u?}. It has no expiry and its device binding cannot be checked here.
"""
import base64
import binascii
import json
import secrets

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY_B64 = "Gb9cxvudwTWSDC55qBKzpS6sYYgfbp4yXAvI7+pP/0Y="
APP_ID = "com.arkiv.player.light"
SESSION_MS = 30 * 24 * 3600 * 1000


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_gate_token(token: str, public_key_b64: str = PUBLIC_KEY_B64) -> tuple[int, str | None] | None:
    """The (telegram id, username) the token was signed for, or None."""
    if not isinstance(token, str):
        return None
    parts = token.strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(_b64url_decode(parts[1]), parts[0].encode("ascii"))
        payload = json.loads(_b64url_decode(parts[0]))
    except (InvalidSignature, binascii.Error, ValueError, UnicodeError):
        return None
    if not isinstance(payload, dict) or payload.get("app") != APP_ID:
        return None
    tg = payload.get("tg")
    if not isinstance(tg, int) or isinstance(tg, bool) or tg <= 0:
        return None
    username = payload.get("u")
    if not isinstance(username, str) or not username.strip():
        username = None
    return tg, username


def default_name(user_id: int, username: str | None) -> str:
    return "@" + username if username else "Usuario " + str(user_id)[-4:]


def new_session_key() -> str:
    return secrets.token_urlsafe(32)
