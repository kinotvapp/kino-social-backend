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
    assert kinds == [("follow", None, 1, "@beto"), ("comment", pid, 2, "@caro")]
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
