import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app.main import create_app


class Headers(dict):
    """Request headers that also carry the logged-in user."""

    user: dict


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@pytest.fixture
def key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def public_b64(key):
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode()


@pytest.fixture
def make_token(key):
    def make(payload: dict, signer=None) -> str:
        head = _b64url(json.dumps(payload).encode())
        sig = (signer or key).sign(head.encode("ascii"))
        return head + "." + _b64url(sig)

    return make


@pytest.fixture
def fetcher():
    """Link previews off unless a test installs its own."""
    return None


@pytest.fixture
def client(tmp_path, public_b64, fetcher):
    app = create_app(str(tmp_path / "t.db"), public_key_b64=public_b64, fetcher=fetcher)
    with TestClient(app) as c:
        c.app_db = app.state.db
        yield c


@pytest.fixture
def login(client, make_token):
    def do(tg: int, username: str | None = "ana") -> dict:
        payload = {"app": "com.arkiv.player.light", "device": "d", "iat": 1, "tg": tg}
        if username:
            payload["u"] = username
        r = client.post("/session", json={"token": make_token(payload)})
        assert r.status_code == 200, r.text
        body = r.json()
        headers = Headers({"Authorization": "Bearer " + body["session"]})
        headers.user = body["user"]
        return headers

    return do
