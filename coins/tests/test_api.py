from coins.tests.conftest import Device


def watch(client, who, block, seconds=600):
    return client.post("/watch", json={"blockId": block, "seconds": seconds}, headers=who["headers"])


def test_requires_a_session(client):
    assert client.get("/balance").status_code == 401
    assert client.post("/watch", json={"blockId": "x" * 8, "seconds": 60}).status_code == 401


def test_login_then_balance_is_zero(client, login):
    ana = login(1)
    r = client.get("/balance", headers=ana["headers"])
    assert r.status_code == 200
    assert r.json()["balance"] == 0 and r.json()["capToday"] == 20 and r.json()["account"] == ana["account"]


def test_bad_token_and_bad_proof_are_refused(client, make_token):
    dev = Device()
    ch = client.post("/session/challenge").json()["challenge"]
    r = client.post("/session", json={"token": "nope", "challenge": ch, "deviceKey": dev.public, "signature": dev.sign(ch)})
    assert r.status_code == 401 and r.json()["detail"] == "bad_token"
    ch = client.post("/session/challenge").json()["challenge"]
    r = client.post("/session", json={"token": make_token(1), "challenge": ch, "deviceKey": dev.public, "signature": "AAAA"})
    assert r.status_code == 401 and r.json()["detail"] == "bad_proof"


def test_a_stolen_token_with_another_key_is_refused(client, make_token):
    first, thief = Device(), Device()
    for dev, expected in ((first, 200), (thief, 401)):
        ch = client.post("/session/challenge").json()["challenge"]
        r = client.post("/session", json={"token": make_token(7, "same-device"), "challenge": ch, "deviceKey": dev.public,
                                          "signature": dev.sign(ch)})
        assert r.status_code == expected


def test_watch_credits_and_replay_does_not(client, login):
    ana = login(1)
    assert watch(client, ana, "block-0001", 600).json()["credited"] == 1
    assert watch(client, ana, "block-0001", 600).json()["credited"] == 0
    assert watch(client, ana, "block-0002", 600).json()["credited"] == 1
    assert client.get("/balance", headers=ana["headers"]).json()["balance"] == 2


def test_watch_rejects_bad_blocks(client, login):
    ana = login(1)
    assert watch(client, ana, "short", 60).status_code == 400
    assert watch(client, ana, "block-0002", 0).status_code == 400
    assert watch(client, ana, "block-0003", 601).status_code == 400


def test_give_moves_a_coin_and_shows_the_post_total(client, login):
    ana, ben = login(1), login(2)
    watch(client, ana, "block-0001", 600)
    r = client.post("/give", json={"to": ben["account"], "postId": "post-5"}, headers=ana["headers"])
    assert r.status_code == 200 and r.json() == {"balance": 0, "postTotal": 1}
    assert client.get("/balance", headers=ben["headers"]).json()["balance"] == 1
    assert client.get("/posts/post-5/total", headers=ben["headers"]).json()["total"] == 1


def test_give_errors(client, login):
    ana, ben = login(1), login(2)
    r = client.post("/give", json={"to": ben["account"], "postId": "p"}, headers=ana["headers"])
    assert r.status_code == 400 and r.json()["detail"] == "no_balance"
    watch(client, ana, "block-0001", 600)
    r = client.post("/give", json={"to": ana["account"], "postId": "p"}, headers=ana["headers"])
    assert r.json()["detail"] == "self_gift"
    r = client.post("/give", json={"to": "zz", "postId": "p"}, headers=ana["headers"])
    assert r.json()["detail"] == "unknown_account"
