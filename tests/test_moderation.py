"""Strikes and mutes (owner, 2026-10-29): a masked post or comment is a strike, the third in 12 h mutes for 12 h."""
import time

from app import moderation

HOUR_S = 3600


def _shift(monkeypatch, seconds: float):
    """Moves every clock db.now_ms reads (time.time) forward by [seconds]."""
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + seconds)


def _post(client, headers, text="hola"):
    return client.post("/posts", json={"text": text}, headers=headers)


def _comment(client, headers, post_id, text):
    return client.post(f"/posts/{post_id}/comments", json={"text": text}, headers=headers)


def test_masked_comment_is_strike_one_of_three(client, login):
    a = login(1)
    pid = _post(client, a).json()["id"]
    r = _comment(client, a, pid, "qué mierda de final")
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "qué xxxx de final"
    assert body["moderation"] == {"strikes": 1, "limit": 3}


def test_one_strike_per_text_however_many_words(client, login):
    a = login(1)
    r = _post(client, a, "mierda, puta, joder")
    assert r.json()["text"] == "xxxx, xxxx, xxxx"
    assert r.json()["moderation"]["strikes"] == 1


def test_device_masked_text_counts(client, login):
    a = login(1)
    r = _post(client, a, "Qué xxxx de capítulo")  # the device's list or Kino's AI masked it already
    assert r.status_code == 200
    assert r.json()["moderation"] == {"strikes": 1, "limit": 3}
    # "xxxx" only as a whole word: neither inside a longer one nor the adult "xxx".
    assert "moderation" not in _post(client, a, "xxxxx y xxx").json()


def test_clean_text_does_not_count(client, login):
    a = login(1)
    r = _post(client, a, "Buenísima la serie 🎬")
    assert r.status_code == 200 and "moderation" not in r.json()
    pid = r.json()["id"]
    assert "moderation" not in _comment(client, a, pid, "Gracias").json()
    assert "mutedUntil" not in client.get("/me", headers=a).json()


def test_community_post_counts(client, login):
    a = login(1)
    c = client.post("/communities", json={"name": "Cine", "slug": "cine"}, headers=a)
    assert c.status_code == 200, c.text
    r = client.post("/posts", json={"text": "qué mierda", "communityId": c.json()["id"]}, headers=a)
    assert r.status_code == 200 and r.json()["moderation"]["strikes"] == 1


def test_third_strike_mutes_and_blocks_posts_and_comments(client, login):
    a = login(1)
    b = login(2, "beto")
    pid = _post(client, b).json()["id"]
    assert _post(client, a, "mierda").json()["moderation"]["strikes"] == 1
    assert _comment(client, a, pid, "xxxx").json()["moderation"]["strikes"] == 2
    third = _comment(client, a, pid, "pendejo")
    assert third.status_code == 200 and third.json()["text"] == "xxxx"  # still saved, masked
    mod = third.json()["moderation"]
    assert mod["strikes"] == 3 and mod["limit"] == 3
    until = mod["mutedUntil"]
    assert abs(until - (int(time.time() * 1000) + moderation.MUTE_MS)) < 60_000
    assert [c["text"] for c in client.get(f"/posts/{pid}/comments", headers=b).json()["items"]] == ["xxxx", "xxxx"]

    for r in (_post(client, a, "hola de nuevo"), _comment(client, a, pid, "hola")):
        assert r.status_code == 403
        assert r.json() == {"error": "muted", "mutedUntil": until}
    assert client.get("/me", headers=a).json()["mutedUntil"] == until
    # The others are not touched.
    assert _post(client, b, "sigo aquí").status_code == 200


def test_mute_clears_strikes(client, login, monkeypatch):
    a = login(1)
    for text in ("mierda", "puta", "joder"):
        _post(client, a, text)
    _shift(monkeypatch, 12 * HOUR_S + 60)
    assert _post(client, a, "mierda").json()["moderation"] == {"strikes": 1, "limit": 3}


def test_likes_and_follows_still_work_while_muted(client, login):
    a = login(1)
    b = login(2, "beto")
    pid = _post(client, b).json()["id"]
    for text in ("mierda", "puta", "joder"):
        _post(client, a, text)
    assert _post(client, a, "hola").status_code == 403
    assert client.post(f"/posts/{pid}/like", headers=a).json() == {"likes": 1, "liked": True}
    assert client.delete(f"/posts/{pid}/like", headers=a).status_code == 200
    assert client.post("/follow/2", headers=a).status_code == 200
    assert client.get("/feed", headers=a).status_code == 200
    assert client.patch("/me", json={"bio": "cinéfila"}, headers=a).status_code == 200


def test_strikes_older_than_12_hours_do_not_count(client, login, monkeypatch):
    a = login(1)
    assert _post(client, a, "mierda").json()["moderation"]["strikes"] == 1
    assert _post(client, a, "puta").json()["moderation"]["strikes"] == 2
    _shift(monkeypatch, 12 * HOUR_S + 1)
    third = _post(client, a, "joder").json()["moderation"]
    assert third == {"strikes": 1, "limit": 3}
    assert _post(client, a, "hola").status_code == 200


def test_strikes_inside_the_window_add_up(client, login, monkeypatch):
    a = login(1)
    _post(client, a, "mierda")
    _shift(monkeypatch, 11 * HOUR_S)
    _post(client, a, "puta")
    third = _post(client, a, "joder").json()["moderation"]
    assert third["strikes"] == 3 and "mutedUntil" in third


def test_mute_ends_after_12_hours(client, login, monkeypatch):
    a = login(1)
    for text in ("mierda", "puta", "joder"):
        _post(client, a, text)
    _shift(monkeypatch, 11 * HOUR_S)
    assert _post(client, a, "hola").status_code == 403
    monkeypatch.undo()
    _shift(monkeypatch, 12 * HOUR_S + 1)
    r = _post(client, a, "hola de nuevo")
    assert r.status_code == 200 and "moderation" not in r.json()
    assert "mutedUntil" not in client.get("/me", headers=a).json()
