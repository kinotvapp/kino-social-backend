from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

GOOD = {"app": "com.arkiv.player.light", "device": "d", "iat": 1, "tg": 123456789, "u": "ana"}


def test_session_creates_user(client, make_token):
    r = client.post("/session", json={"token": make_token(GOOD)})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["name"] == "@ana"
    assert body["user"]["id"] == 123456789
    again = client.post("/session", json={"token": make_token(GOOD)}).json()
    assert again["user"]["id"] == body["user"]["id"]
    assert again["session"] != body["session"]


def test_default_name_without_username(client, make_token):
    payload = {k: v for k, v in GOOD.items() if k != "u"}
    r = client.post("/session", json={"token": make_token(payload)})
    assert r.json()["user"]["name"] == "Usuario 6789"


def test_session_rejects_bad_tokens(client, make_token):
    other = Ed25519PrivateKey.generate()
    good = make_token(GOOD)
    head, sig = good.split(".")
    edited = make_token({**GOOD, "tg": 1})[: len(head)]  # a different payload under the original signature
    bad = [
        make_token(GOOD, signer=other),
        edited + "." + sig,
        make_token({k: v for k, v in GOOD.items() if k != "tg"}),
        make_token({**GOOD, "tg": 0}),
        make_token({**GOOD, "app": "other.app"}),
        "garbage",
        good + ".x",
        "",
    ]
    for token in bad:
        assert client.post("/session", json={"token": token}).status_code == 401, token
    assert client.app_db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_bearer_required(client, login):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    h = login(5)
    assert client.get("/me", headers=h).json()["id"] == 5
