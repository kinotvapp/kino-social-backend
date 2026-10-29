import time


def now():
    return int(time.time() * 1000)


def _posts(client, by, n, start):
    for i in range(n):
        client.app_db.execute("INSERT INTO posts (user_id, text, created_at) VALUES (?, ?, ?)", (by, f"u{by}-{i}", start + i))


def test_no_more_than_two_in_a_row_from_one_person(client, login):
    viewer = login(1)
    for tg in (2, 3, 4):
        login(tg)
    _posts(client, 2, 8, 1000)  # a burst from one person, newest of all
    _posts(client, 3, 2, 500)
    _posts(client, 4, 2, 400)
    items = [i for i in client.get("/feed", headers=viewer).json()["items"] if i["type"] == "post"]
    authors = [i["user"]["id"] for i in items]
    assert sorted(authors) == sorted([2] * 8 + [3] * 2 + [4] * 2)  # nothing dropped
    runs = max(len(r) for r in "".join(map(str, authors)).replace("3", " ").replace("4", " ").split())
    assert runs <= 3  # the 8 are spread while others remain to interleave
    assert authors[:3] != [2, 2, 2]


def test_paging_never_loses_an_item_after_mixing(client, login):
    viewer = login(1)
    login(2)
    login(3)
    _posts(client, 2, 30, 2000)
    _posts(client, 3, 15, 1000)
    seen, before = [], None
    while True:
        page = client.get("/feed" + (f"?before={before}" if before else ""), headers=viewer).json()
        seen += [i["text"] for i in page["items"] if i["type"] == "post"]
        if page["next"] is None:
            break
        before = page["next"]
    assert sorted(seen) == sorted([f"u2-{i}" for i in range(30)] + [f"u3-{i}" for i in range(15)])


def test_episodes_of_one_series_collapse_into_one_card(client, login):
    a = login(1)
    b = login(2)
    client.post("/follow/2", headers=a)
    items = [{"kind": "series", "tmdbId": 1399, "title": "Juego de tronos", "season": 1, "episode": e, "watchedAtMs": now()}
             for e in (1, 2, 3, 4)]
    client.post("/activity", json={"items": items}, headers=b)
    acts = [i for i in client.get("/feed", headers=a).json()["items"] if i["type"] == "activity"]
    assert len(acts) == 1 and acts[0]["episodes"] == 4 and acts[0]["episode"] == 4


def test_suggestions_while_following_few(client, login):
    a = login(1)
    for tg in (2, 3, 4):
        h = login(tg, f"user{tg}")
        client.post("/posts", json={"text": f"hola de {tg}"}, headers=h)
    items = client.get("/feed", headers=a).json()["items"]
    sugg = [i for i in items if i["type"] == "suggestions"]
    assert len(sugg) == 1 and {p["id"] for p in sugg[0]["people"]} == {2, 3, 4}
    for tg in (2, 3, 4):
        client.post(f"/follow/{tg}", headers=a)
    assert [i for i in client.get("/feed", headers=a).json()["items"] if i["type"] == "suggestions"] == []
