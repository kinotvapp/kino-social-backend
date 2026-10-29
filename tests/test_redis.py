"""The same app with the Redis cache and budgets (skipped when the local Redis is not reachable)."""
import os

import pytest

from app.main import create_app
from fastapi.testclient import TestClient


def _redis():
    redis = pytest.importorskip("redis")
    url = os.environ.get("KINO_SOCIAL_REDIS")
    if not url:
        path = os.path.join(os.path.dirname(__file__), "..", "data", "pg.env")
        try:
            with open(path, encoding="ascii") as f:
                url = next((l.split("=", 1)[1].strip() for l in f if l.startswith("KINO_SOCIAL_REDIS=")), None)
        except FileNotFoundError:
            url = None
    if not url:
        pytest.skip("no Redis configured")
    client = redis.Redis.from_url(url.rsplit("/", 1)[0] + "/15", socket_timeout=1)
    try:
        client.ping()
    except Exception:
        pytest.skip("Redis not reachable")
    client.flushdb()
    return client


@pytest.fixture
def redis_client(database, public_b64):
    r = _redis()
    database.truncate_all()
    app = create_app(database, public_key_b64=public_b64, secret=b"test-secret", redis_client=r)
    with TestClient(app) as c:
        c.app_db = database
        yield c
    r.flushdb()


def test_redis_cache_serves_304_and_scopes(redis_client, make_token, device):
    client = redis_client
    dev = device()
    ch = client.post("/session/challenge").json()["challenge"]
    payload = {"app": "com.arkiv.player.light", "device": "d", "iat": 1, "tg": 42}
    s = client.post("/session", json={"token": make_token(payload), "challenge": ch, "deviceKey": dev.public, "author": "",
                                      "codeHash": "ab" * 32, "signature": dev.sign(ch)}).json()
    h = {"Authorization": "Bearer " + s["session"]}
    p1 = client.post("/posts", json={"text": "uno"}, headers=h).json()["id"]
    p2 = client.post("/posts", json={"text": "dos"}, headers=h).json()["id"]
    tag = client.get("/feed", headers=h).headers["etag"]
    assert client.get("/feed", headers={**h, "If-None-Match": tag}).status_code == 304
    c2 = client.get(f"/posts/{p2}/comments", headers=h).headers["etag"]
    client.post(f"/posts/{p1}/comments", json={"text": "hola"}, headers=h)
    assert client.get("/feed", headers={**h, "If-None-Match": tag}).status_code == 200
    assert client.get(f"/posts/{p2}/comments", headers={**h, "If-None-Match": c2}).status_code == 304
    codes = [client.post("/posts", json={"text": f"n{i}"}, headers=h).status_code for i in range(10)]
    assert codes.count(429) == 2  # 10 an hour, shared through Redis
