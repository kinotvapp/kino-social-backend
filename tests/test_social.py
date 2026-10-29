import time

DAY = 86_400_000


def now():
    return int(time.time() * 1000)


def test_post_and_comment_flow(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    post = client.post("/posts", json={"text": "Hola mundo 🎬"}, headers=a)
    assert post.status_code == 200
    pid = post.json()["id"]
    assert client.post(f"/posts/{pid}/comments", json={"text": "Buenísimo"}, headers=b).status_code == 200
    assert client.post(f"/posts/{pid}/comments", json={"text": "Gracias"}, headers=a).status_code == 200
    comments = client.get(f"/posts/{pid}/comments", headers=b).json()["items"]
    assert [c["text"] for c in comments] == ["Buenísimo", "Gracias"]
    assert comments[0]["user"]["name"] == "beto"
    feed = client.get("/feed", headers=b).json()["items"]
    assert feed[0]["type"] == "post" and feed[0]["comments"] == 2 and feed[0]["user"]["name"] == "ana"
    assert client.delete(f"/posts/{pid}", headers=b).status_code == 403
    assert client.delete(f"/posts/{pid}", headers=a).status_code == 200
    assert client.get("/feed", headers=b).json()["items"] == []


def test_post_text_is_masked(client, login):
    a = login(1)
    assert client.post("/posts", json={"text": "qué mierda de final"}, headers=a).json()["text"] == "qué xxxx de final"


def test_text_limits(client, login):
    a = login(1)
    assert client.post("/posts", json={"text": "x" * 501}, headers=a).status_code == 400
    assert client.post("/posts", json={"text": "   "}, headers=a).status_code == 400
    pid = client.post("/posts", json={"text": "ok"}, headers=a).json()["id"]
    assert client.post(f"/posts/{pid}/comments", json={"text": "y" * 301}, headers=a).status_code == 400
    assert client.patch("/me", json={"name": "z" * 41}, headers=a).status_code == 400
    assert client.patch("/me", json={"name": "Ana María"}, headers=a).json()["name"] == "Ana María"
    assert client.patch("/me", json={"bio": "b" * 161}, headers=a).status_code == 400
    me = client.patch("/me", json={"bio": "Fan del cine y de la mierda de finales"}, headers=a).json()
    assert me["bio"] == "Fan del cine y de la xxxx de finales"
    assert client.get("/users/1", headers=a).json()["user"]["bio"] == me["bio"]


def _watch(client, headers, **item):
    base = {"kind": "series", "tmdbId": 66732, "title": "Stranger Things", "season": 4, "episode": 3,
            "poster": "https://image.tmdb.org/t/p/w342/x.jpg", "watchedAtMs": now()}
    return client.post("/activity", json={"items": [{**base, **item}]}, headers=headers)


def test_activity_visibility(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    assert _watch(client, b).json()["added"] == 1
    assert [i for i in client.get("/feed", headers=a).json()["items"] if i["type"] == "activity"] == []
    assert client.post("/follow/2", headers=a).status_code == 200
    items = client.get("/feed", headers=a).json()["items"]
    assert items[0]["type"] == "activity" and items[0]["season"] == 4 and items[0]["episode"] == 3
    assert items[0]["poster"].startswith("https://image.tmdb.org/")
    act_id = client.get("/me/activity", headers=b).json()["items"][0]["id"]
    assert client.patch(f"/me/activity/{act_id}", json={"hidden": True}, headers=b).status_code == 200
    assert client.get("/feed", headers=a).json()["items"] == []
    assert client.get("/users/2", headers=a).json()["activity"] == []
    client.patch(f"/me/activity/{act_id}", json={"hidden": False}, headers=b)
    client.patch("/me", json={"shareActivity": False}, headers=b)
    assert client.get("/feed", headers=a).json()["items"] == []
    assert client.get("/users/2", headers=a).json()["activity"] == []
    assert client.get("/users/2", headers=b).json()["activity"] != []  # the owner still sees their own


def test_adult_activity_dropped(client, login):
    b = login(2)
    assert _watch(client, b, adult=True).json()["added"] == 0
    assert _watch(client, b, tmdbId=9, title="XXX Porn Party").json()["added"] == 0
    assert _watch(client, b, tmdbId=None).json()["added"] == 0
    r = _watch(client, b, tmdbId=10, title="Dune", kind="movie", season=None, episode=None, poster="https://evil.example/x.jpg")
    assert r.json()["added"] == 1
    assert client.get("/me/activity", headers=b).json()["items"][0]["poster"] is None


def test_duplicate_activity_ignored(client, login):
    b = login(2)
    assert _watch(client, b).json()["added"] == 1
    assert _watch(client, b).json()["added"] == 0
    assert _watch(client, b, episode=4).json()["added"] == 1
    movie = dict(kind="movie", tmdbId=438631, title="Dune", season=None, episode=None)
    assert _watch(client, b, **movie).json()["added"] == 1
    assert _watch(client, b, **movie).json()["added"] == 0


def test_old_activity_not_in_feed(client, login):
    a = login(1)
    b = login(2)
    client.post("/follow/2", headers=a)
    _watch(client, b, watchedAtMs=now() - 10 * DAY)
    assert client.get("/feed", headers=a).json()["items"] == []
    assert len(client.get("/users/2", headers=a).json()["activity"]) == 1


def test_follow_rules_and_people(client, login):
    a = login(1, "ana")
    login(2, "beto")
    assert client.post("/follow/1", headers=a).status_code == 400
    assert client.post("/follow/999", headers=a).status_code == 404
    client.post("/follow/2", headers=a)
    profile = client.get("/users/2", headers=a).json()
    assert profile["following"] is True and profile["followers"] == 1
    people = client.get("/people", headers=a).json()["items"]
    assert [(p["id"], p["following"]) for p in people] == [(2, True)]
    client.delete("/follow/2", headers=a)
    assert client.get("/users/2", headers=a).json()["following"] is False


def test_feed_pages(client, login):
    a = login(1)
    for i in range(25):
        client.app_db.execute("INSERT INTO posts (user_id, text, created_at) VALUES (1, ?, ?)", (f"p{i}", 1000 + i))
    first = client.get("/feed?limit=20", headers=a).json()
    assert len(first["items"]) == 20 and first["items"][0]["text"] == "p24"
    second = client.get(f"/feed?before={first['next']}", headers=a).json()
    assert [i["text"] for i in second["items"]] == [f"p{i}" for i in range(4, -1, -1)]
    assert second["next"] is None


def test_rate_limit_posts(client, login):
    a = login(1)
    codes = [client.post("/posts", json={"text": f"n{i}"}, headers=a).status_code for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
