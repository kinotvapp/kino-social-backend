def test_comment_and_follow_notify_the_owner_grouped(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    c = login(3, "caro")
    pid = client.post("/posts", json={"text": "Hola"}, headers=a).json()["id"]
    client.post(f"/posts/{pid}/comments", json={"text": "uno"}, headers=b)
    client.post(f"/posts/{pid}/comments", json={"text": "dos"}, headers=c)
    client.post(f"/posts/{pid}/comments", json={"text": "mío"}, headers=a)  # own comment: no notification
    client.post("/follow/1", headers=b)
    body = client.get("/notifications", headers=a).json()
    assert body["unread"] == 2
    kinds = [(n["kind"], n.get("postId"), n["count"], n["actor"]["name"]) for n in body["items"]]
    assert kinds == [("follow", None, 1, "beto"), ("comment", pid, 2, "caro")]
    assert client.get("/notifications", headers=b).json()["items"] == []


def test_read_and_since(client, login):
    a = login(1)
    b = login(2)
    pid = client.post("/posts", json={"text": "Hola"}, headers=a).json()["id"]
    client.post(f"/posts/{pid}/comments", json={"text": "uno"}, headers=b)
    first = client.get("/notifications", headers=a).json()["items"][0]
    assert client.post("/notifications/read", json={"upTo": first["createdAt"]}, headers=a).status_code == 200
    body = client.get("/notifications", headers=a).json()
    assert body["unread"] == 0 and body["items"][0]["read"] is True
    client.post(f"/posts/{pid}/comments", json={"text": "otro"}, headers=b)  # a read group starts a new one
    body = client.get(f"/notifications?since={first['createdAt']}", headers=a).json()
    assert body["unread"] == 1 and len(body["items"]) == 1 and body["items"][0]["count"] == 1


def test_unfollow_then_follow_again_does_not_spam(client, login):
    a = login(1)
    b = login(2)
    for _ in range(3):
        client.post("/follow/1", headers=b)
        client.delete("/follow/1", headers=b)
    client.post("/follow/1", headers=b)
    assert len(client.get("/notifications", headers=a).json()["items"]) == 1


def test_a_new_post_tells_followers_once_per_author_until_read(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    client.post("/follow/2", headers=a)
    first = client.post("/posts", json={"text": "Primera"}, headers=b).json()
    second = client.post("/posts", json={"text": "Segunda"}, headers=b).json()
    items = [n for n in client.get("/notifications", headers=a).json()["items"] if n["kind"] == "post"]
    assert len(items) == 1 and items[0]["count"] == 2 and items[0]["postId"] == second["id"]
    assert items[0]["actor"]["id"] == 2 and items[0]["postText"] == "Segunda"
    client.post("/notifications/read", json={"upTo": items[0]["createdAt"]}, headers=a)
    client.post("/posts", json={"text": "Tercera"}, headers=b)
    fresh = [n for n in client.get("/notifications", headers=a).json()["items"] if n["kind"] == "post" and not n["read"]]
    assert len(fresh) == 1 and fresh[0]["count"] == 1
    # The author is never told about their own post, and someone who does not follow hears nothing.
    assert [n for n in client.get("/notifications", headers=b).json()["items"] if n["kind"] == "post"] == []
    assert first["id"] != second["id"]
