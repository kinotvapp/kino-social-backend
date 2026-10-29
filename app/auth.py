"""Who is calling: Kino's gate token, a proof that the caller holds this install's key, and this server's own sessions.

Gate token (signed by @kinocodigo_bot): base64url(JSON payload) + "." + base64url(Ed25519 signature of the first part's
ASCII bytes), payload {app, device, iat, tg, u?}. It never expires and anyone holding it could replay it, so a session also
needs a fresh challenge signed by the install's own P-256 key for this plugin (kino.identity.telegram({ challenge })). The
first key seen for a token's `device` is bound to it; a different key for that device is refused. The Telegram id itself is
never stored: users are keyed by HMAC(server secret, telegram id).
"""
import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY_B64 = "Gb9cxvudwTWSDC55qBKzpS6sYYgfbp4yXAvI7+pP/0Y="
APP_ID = "com.arkiv.player.light"
PLUGIN_ID = "kino-social"
SESSION_MS = 30 * 24 * 3600 * 1000
CHALLENGE_MS = 2 * 60 * 1000
MAX_DEVICES = 6


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def now_ms() -> int:
    return int(time.time() * 1000)


def verify_gate_token(token, public_key_b64: str = PUBLIC_KEY_B64) -> tuple[int, str] | None:
    """(telegram id, device hash) the token was signed for, or None."""
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
    tg, device = payload.get("tg"), payload.get("device")
    if not isinstance(tg, int) or isinstance(tg, bool) or tg <= 0 or not isinstance(device, str) or not device:
        return None
    return tg, device[:128]


def signed_message(challenge: str, author: str, code_hash: str, plugin_id: str = PLUGIN_ID) -> bytes:
    """What Kino signs with the install's key: the plugin, the challenge, and which code asked (its verified author's
    fingerprint, "" when unsigned, and the SHA-256 of the entry file Kino runs)."""
    return f"kino-identity-v2\n{plugin_id}\n{challenge}\n{author}\n{code_hash}".encode()


def verify_device_signature(device_key_b64, signature_b64, challenge, author="", code_hash="", plugin_id: str = PLUGIN_ID) -> bool:
    """SHA256withECDSA by [device_key_b64] (X.509 SPKI, P-256) over [signed_message]."""
    if not all(isinstance(v, str) and v for v in (device_key_b64, signature_b64, challenge)):
        return False
    if not isinstance(author, str) or not isinstance(code_hash, str) or len(author) > 128 or len(code_hash) > 128:
        return False
    try:
        key = serialization.load_der_public_key(_b64url_decode(device_key_b64))
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            return False
        key.verify(_b64url_decode(signature_b64), signed_message(challenge, author, code_hash, plugin_id), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, binascii.Error, TypeError):
        return False


def pseudonym(telegram_id: int, plugin_id: str = PLUGIN_ID) -> str:
    """kino.identity.telegram()'s `id`: the same per-plugin pseudonym Kino hands the plugin."""
    return hashlib.sha256(f"kino-identity-v1\n{plugin_id}\n{telegram_id}".encode()).hexdigest()[:32]


def user_key(secret: bytes, telegram_id: int) -> str:
    """How this server stores a person: an HMAC nobody can turn back into the Telegram id without the secret."""
    return hmac.new(secret, str(telegram_id).encode(), hashlib.sha256).hexdigest()


def default_name(telegram_id: int) -> str:
    return "Usuario " + pseudonym(telegram_id)[:4].upper()


def new_session_key() -> str:
    return secrets.token_urlsafe(32)


def new_challenge() -> str:
    return secrets.token_urlsafe(24)
