def test_like_unlike_counts_and_flag(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    pid = client.post("/posts", json={"text": "Hola"}, headers=a).json()["id"]
    assert client.post(f"/posts/{pid}/like", headers=b).json() == {"likes": 1, "liked": True}
    assert client.post(f"/posts/{pid}/like", headers=b).json() == {"likes": 1, "liked": True}  # idempotent
    assert client.post(f"/posts/{pid}/like", headers=a).json() == {"likes": 2, "liked": True}
    seen_by_b = client.get("/feed", headers=b).json()["items"][0]
    assert seen_by_b["likes"] == 2 and seen_by_b["liked"] is True
    assert client.delete(f"/posts/{pid}/like", headers=b).json() == {"likes": 1, "liked": False}
    assert client.get("/feed", headers=b).json()["items"][0]["liked"] is False
    assert client.post("/posts/999/like", headers=b).status_code == 404


def test_likes_notify_the_author_grouped(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    c = login(3, "caro")
    pid = client.post("/posts", json={"text": "Hola"}, headers=a).json()["id"]
    client.post(f"/posts/{pid}/like", headers=a)  # own like: no notice
    client.post(f"/posts/{pid}/like", headers=b)
    client.post(f"/posts/{pid}/like", headers=c)
    items = client.get("/notifications", headers=a).json()["items"]
    assert [(n["kind"], n["count"], n["actor"]["name"]) for n in items] == [("like", 2, "caro")]
    client.delete(f"/posts/{pid}/like", headers=c)
    client.post(f"/posts/{pid}/like", headers=c)  # like again: no second notice from the same person
    assert client.get("/notifications", headers=a).json()["items"][0]["count"] == 2


def test_like_rate_limit(client, login):
    a = login(1)
    ids = [client.post("/posts", json={"text": f"p{i}"}, headers=a).json()["id"] for i in range(3)]
    b = login(2)
    codes = []
    for i in range(61):
        pid = ids[i % 3]
        codes.append(client.post(f"/posts/{pid}/like", headers=b).status_code)
        client.delete(f"/posts/{pid}/like", headers=b)
    assert 429 in codes
