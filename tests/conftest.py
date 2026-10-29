import base64
import json
import os

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app import db
from app.main import create_app

PLUGIN_ID = "kino-social"


def _test_dsn() -> str:
    if os.environ.get("KINO_SOCIAL_TEST_DSN"):
        return os.environ["KINO_SOCIAL_TEST_DSN"]
    path = os.path.join(os.path.dirname(__file__), "..", "data", "pg.env")
    with open(path, encoding="ascii") as f:
        for line in f:
            if line.startswith("KINO_SOCIAL_TEST_DSN="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("no test database: set KINO_SOCIAL_TEST_DSN or data/pg.env")


@pytest.fixture(scope="session")
def database():
    database = db.connect(_test_dsn())
    yield database
    database.close()


class Headers(dict):
    """Request headers that also carry the logged-in user."""

    user: dict


def b64url(data: bytes) -> str:
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
        head = b64url(json.dumps(payload).encode())
        sig = (signer or key).sign(head.encode("ascii"))
        return head + "." + b64url(sig)

    return make


class Device:
    """One install of Kino: its P-256 key for this plugin, as kino.identity.telegram({ challenge }) uses it."""

    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        der = self.private.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        self.public = b64url(der)

    def sign(self, challenge: str, plugin_id: str = PLUGIN_ID, author: str = "", code_hash: str = "ab" * 32) -> str:
        message = f"kino-identity-v2\n{plugin_id}\n{challenge}\n{author}\n{code_hash}".encode()
        return b64url(self.private.sign(message, ec.ECDSA(hashes.SHA256())))


@pytest.fixture
def device():
    return Device


@pytest.fixture
def fetcher():
    """Link previews off unless a test installs its own."""
    return None


@pytest.fixture
def client(database, public_b64, fetcher):
    database.truncate_all()
    app = create_app(database, public_key_b64=public_b64, fetcher=fetcher, secret=b"test-secret")
    with TestClient(app) as c:
        c.app_db = database
        yield c


@pytest.fixture
def session_for(client, make_token):
    """POST /session the way the plugin does: a challenge, then the token and the device's signature over it."""

    def start(tg: int, dev: Device, *, device_hash: str = "d1", challenge: str | None = None, signature: str | None = None,
              author: str = "", iat: int = 1):
        payload = {"app": "com.arkiv.player.light", "device": device_hash, "iat": iat, "tg": tg}
        ch = challenge or client.post("/session/challenge").json()["challenge"]
        return client.post("/session", json={
            "token": make_token(payload), "challenge": ch, "deviceKey": dev.public, "author": author, "codeHash": "ab" * 32,
            "signature": signature if signature is not None else dev.sign(ch, author=author),
        })

    return start


@pytest.fixture
def login(client, session_for):
    devices: dict[int, Device] = {}

    def do(tg: int, name: str | None = "ana") -> Headers:
        dev = devices.setdefault(tg, Device())
        r = session_for(tg, dev, device_hash=f"dev-{tg}")
        assert r.status_code == 200, r.text
        body = r.json()
        headers = Headers({"Authorization": "Bearer " + body["session"]})
        if name:
            assert client.patch("/me", json={"name": name}, headers=headers).status_code == 200
            body["user"]["name"] = name
        headers.user = body["user"]
        return headers

    return do
