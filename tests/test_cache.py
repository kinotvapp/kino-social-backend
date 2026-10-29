def test_feed_etag_304_until_something_changes(client, login):
    a = login(1)
    client.post("/posts", json={"text": "uno"}, headers=a)
    first = client.get("/feed", headers=a)
    tag = first.headers["etag"]
    again = client.get("/feed", headers={**a, "If-None-Match": tag})
    assert again.status_code == 304 and again.content == b""
    client.post("/posts", json={"text": "dos"}, headers=a)
    fresh = client.get("/feed", headers={**a, "If-None-Match": tag})
    assert fresh.status_code == 200 and fresh.headers["etag"] != tag
    assert [i["text"] for i in fresh.json()["items"]] == ["dos", "uno"]


def test_feed_etag_is_per_viewer_and_page(client, login):
    a = login(1)
    b = login(2)
    client.post("/posts", json={"text": "uno"}, headers=a)
    tag_a = client.get("/feed", headers=a).headers["etag"]
    assert client.get("/feed", headers={**b, "If-None-Match": tag_a}).status_code == 200
    assert client.get("/feed?before=5", headers={**a, "If-None-Match": tag_a}).status_code == 200


def test_comments_etag_changes_with_a_comment_and_a_follow_changes_feed(client, login):
    a = login(1)
    b = login(2)
    pid = client.post("/posts", json={"text": "uno"}, headers=a).json()["id"]
    tag = client.get(f"/posts/{pid}/comments", headers=a).headers["etag"]
    assert client.get(f"/posts/{pid}/comments", headers={**a, "If-None-Match": tag}).status_code == 304
    client.post(f"/posts/{pid}/comments", json={"text": "hola"}, headers=b)
    assert client.get(f"/posts/{pid}/comments", headers={**a, "If-None-Match": tag}).status_code == 200
    feed_tag = client.get("/feed", headers=a).headers["etag"]
    client.post("/follow/2", headers=a)
    assert client.get("/feed", headers={**a, "If-None-Match": feed_tag}).status_code == 200
