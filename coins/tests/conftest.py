import base64
import json
import os

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from coins import db as coins_db
from coins.main import create_app

PLUGIN_ID = "kino-coins"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _test_dsn() -> str:
    if os.environ.get("KINO_COINS_TEST_DSN"):
        return os.environ["KINO_COINS_TEST_DSN"]
    path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "pg.env")
    with open(path, encoding="ascii") as f:
        for line in f:
            if line.startswith("KINO_SOCIAL_TEST_DSN="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("no test database: set KINO_COINS_TEST_DSN or data/pg.env")


@pytest.fixture(scope="session")
def database():
    database = coins_db.connect(_test_dsn())
    yield database
    database.close()


@pytest.fixture
def clean(database):
    database.truncate_all()
    return database


class Device:
    """One install of Kino: its P-256 key, as the coins capability signs a challenge with it."""

    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        der = self.private.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        self.public = b64url(der)

    def sign(self, challenge: str, plugin_id: str = PLUGIN_ID, author: str = "", code_hash: str = "") -> str:
        message = f"kino-identity-v2\n{plugin_id}\n{challenge}\n{author}\n{code_hash}".encode()
        return b64url(self.private.sign(message, ec.ECDSA(hashes.SHA256())))


@pytest.fixture
def bot_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def make_token(bot_key):
    def make(tg: int, device_hash: str = "d1", iat: int = 1) -> str:
        payload = {"app": "com.arkiv.player.light", "device": device_hash, "iat": iat, "tg": tg}
        head = b64url(json.dumps(payload).encode())
        return head + "." + b64url(bot_key.sign(head.encode("ascii")))

    return make


@pytest.fixture
def client(clean, bot_key):
    public = base64.b64encode(bot_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    app = create_app(clean, public_key_b64=public, secret=b"test-secret")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def login(client, make_token):
    """A session as the app starts it: a challenge, then the token and the device's signature over it."""
    devices: dict[int, Device] = {}

    def do(tg: int) -> dict:
        dev = devices.setdefault(tg, Device())
        ch = client.post("/session/challenge").json()["challenge"]
        r = client.post("/session", json={
            "token": make_token(tg, f"dev-{tg}"), "challenge": ch, "deviceKey": dev.public, "signature": dev.sign(ch),
        })
        assert r.status_code == 200, r.text
        body = r.json()
        return {"headers": {"Authorization": "Bearer " + body["session"]}, "account": body["account"]}

    return do
