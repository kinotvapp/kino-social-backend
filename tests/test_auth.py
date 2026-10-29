import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import auth

TG = 123456789
GOOD = {"app": "com.arkiv.player.light", "device": "d1", "iat": 1, "tg": TG, "u": "ana"}


def test_session_creates_user_without_storing_the_telegram_id(client, session_for, device):
    dev = device()
    r = session_for(TG, dev)
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["id"] == 1
    assert user["name"] == "Usuario " + auth.pseudonym(TG)[:4].upper()  # never the @username
    again = session_for(TG, dev).json()
    assert again["user"]["id"] == 1 and again["session"] != r.json()["session"]
    dump = "\n".join(str(tuple(row.values())) for t in ("users", "sessions", "devices")
                     for row in client.app_db.execute(f"SELECT * FROM {t}"))
    assert str(TG) not in dump and "ana" not in dump


def test_pseudonym_matches_kino():
    # The same formula as kino.identity.telegram()'s id (Kino and the kit pin this vector too).
    assert auth.pseudonym(TG, "kino-social") == auth.pseudonym(TG)
    assert len(auth.pseudonym(TG)) == 32 and auth.pseudonym(TG) != auth.pseudonym(TG, "other")


def test_session_rejects_bad_tokens(client, make_token, device):
    dev = device()
    other = Ed25519PrivateKey.generate()
    good = make_token(GOOD)
    head, sig = good.split(".")
    edited = make_token({**GOOD, "tg": 1}).split(".")[0]
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
        ch = client.post("/session/challenge").json()["challenge"]
        r = client.post("/session", json={"token": token, "challenge": ch, "deviceKey": dev.public, "signature": dev.sign(ch)})
        assert r.status_code == 401, token
    assert client.app_db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_signature_must_come_from_the_device_key(client, session_for, device):
    dev, thief = device(), device()
    ch = client.post("/session/challenge").json()["challenge"]
    assert session_for(TG, dev, challenge=ch, signature=thief.sign(ch)).status_code == 401  # signed by another key
    ch = client.post("/session/challenge").json()["challenge"]
    assert session_for(TG, dev, challenge=ch, signature=dev.sign(ch, plugin_id="other")).status_code == 401
    assert session_for(TG, dev, signature="").status_code == 401


def test_stolen_token_from_another_key_is_refused(client, session_for, device):
    owner, thief = device(), device()
    assert session_for(TG, owner, device_hash="phone").status_code == 200
    # Same token (same device hash), another key: the device is already bound to the owner's key.
    r = session_for(TG, thief, device_hash="phone")
    assert r.status_code == 401 and r.json()["error"] == "device_key_mismatch"
    assert session_for(TG, owner, device_hash="phone").status_code == 200


def test_challenge_is_single_use_and_expires(client, session_for, device, monkeypatch):
    dev = device()
    ch = client.post("/session/challenge").json()["challenge"]
    assert session_for(TG, dev, challenge=ch).status_code == 200
    assert session_for(TG, dev, challenge=ch).status_code == 401  # replayed
    assert session_for(TG, dev, challenge="madeup-challenge-000").status_code == 401
    ch = client.post("/session/challenge").json()["challenge"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + auth.CHALLENGE_MS / 1000 + 1)
    assert session_for(TG, dev, challenge=ch).status_code == 401


def test_only_the_official_plugin_may_log_in(client, session_for, device):
    client.app.state.allowed_authors = {"07DA-B67F-2ACF-B4D3"}
    dev = device()
    r = session_for(TG, dev, author="")
    assert r.status_code == 403 and r.json()["error"] == "unofficial_plugin"
    assert session_for(TG, dev, author="1111-2222-3333-4444").status_code == 403
    assert session_for(TG, dev, author="07DA-B67F-2ACF-B4D3").status_code == 200
    # The author is inside what Kino signed: claiming it with a signature over "" fails.
    ch = client.post("/session/challenge").json()["challenge"]
    forged = session_for(TG, dev, challenge=ch, signature=dev.sign(ch, author=""), author="07DA-B67F-2ACF-B4D3")
    assert forged.status_code == 401


def test_at_most_six_devices(client, session_for, device):
    for i in range(6):
        assert session_for(TG, device(), device_hash=f"dev{i}").status_code == 200
    r = session_for(TG, device(), device_hash="dev7")
    assert r.status_code == 403 and r.json()["error"] == "too_many_devices"


def test_logout_everywhere(client, login):
    a = login(5)
    assert client.get("/me", headers=a).status_code == 200
    assert client.post("/me/logout-all", headers=a).status_code == 200
    assert client.get("/me", headers=a).status_code == 401


def test_bearer_required(client, login):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/me", headers=login(5)).json()["id"] == 1


def test_login_attempts_are_limited_per_address(client):
    codes = [client.post("/session/challenge", headers={"cf-connecting-ip": "203.0.113.9"}).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    assert client.post("/session/challenge", headers={"cf-connecting-ip": "203.0.113.10"}).status_code == 200
