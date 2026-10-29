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


def test_scoped_versions_keep_other_caches(client, login):
    a = login(1)
    b = login(2)
    p1 = client.post("/posts", json={"text": "uno"}, headers=a).json()["id"]
    p2 = client.post("/posts", json={"text": "dos"}, headers=a).json()["id"]
    tag2 = client.get(f"/posts/{p2}/comments", headers=a).headers["etag"]
    people = client.get("/people", headers=b).headers["etag"]
    client.post(f"/posts/{p1}/comments", json={"text": "hola"}, headers=b)  # a comment on p1 ...
    client.post(f"/posts/{p1}/like", headers=b)  # ... and a like on p1
    assert client.get(f"/posts/{p2}/comments", headers={**a, "If-None-Match": tag2}).status_code == 304  # p2 untouched
    assert client.get("/people", headers={**b, "If-None-Match": people}).status_code == 304


def test_profile_and_notifications_have_etags(client, login):
    a = login(1)
    b = login(2)
    prof = client.get("/users/1", headers=b).headers["etag"]
    notes = client.get("/notifications", headers=a).headers["etag"]
    assert client.get("/users/1", headers={**b, "If-None-Match": prof}).status_code == 304
    assert client.get("/notifications", headers={**a, "If-None-Match": notes}).status_code == 304
    client.post("/follow/1", headers=b)
    assert client.get("/users/1", headers={**b, "If-None-Match": prof}).status_code == 200
    assert client.get("/notifications", headers={**a, "If-None-Match": notes}).status_code == 200


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
