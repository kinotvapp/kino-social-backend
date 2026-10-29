"""Communities: anyone creates one, anyone joins and reads it, members post, the owner moderates; few, grouped notices."""


def make(client, headers, name="Cine de terror", slug="terror", description="Para los que no duermen"):
    return client.post("/communities", json={"name": name, "slug": slug, "description": description}, headers=headers)


def test_create_join_post_and_read_the_community_feed(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    r = make(client, a)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["slug"] == "terror" and c["members"] == 1 and c["joined"] is True and c["owner"] is True
    assert make(client, b, slug="terror").json()["error"] == "slug_taken"
    # Not a member yet: can read, cannot post.
    assert client.post("/posts", json={"text": "hola", "communityId": c["id"]}, headers=b).status_code == 403
    assert client.post(f"/communities/{c['id']}/join", headers=b).json()["members"] == 2
    p = client.post("/posts", json={"text": "Recomienden algo 👻", "communityId": c["id"]}, headers=b).json()
    assert p["community"] == {"id": c["id"], "name": "Cine de terror", "slug": "terror"}
    feed = client.get(f"/communities/{c['id']}/feed", headers=a).json()["items"]
    assert [i["id"] for i in feed] == [p["id"]]
    # Community posts stay in their community: never in the general feed.
    assert [i for i in client.get("/feed", headers=a).json()["items"] if i["type"] == "post"] == []
    # Likes and comments work as on any post.
    assert client.post(f"/posts/{p['id']}/like", headers=a).json()["likes"] == 1
    assert client.post(f"/posts/{p['id']}/comments", json={"text": "Hereditary"}, headers=a).status_code == 200


def test_lists_search_and_leaving(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    c = make(client, a).json()
    make(client, b, name="Anime de los 90", slug="anime90", description="")
    lists = client.get("/communities", headers=a).json()
    assert [x["slug"] for x in lists["mine"]] == ["terror"]
    assert [x["slug"] for x in lists["discover"]] == ["anime90"]
    assert [x["slug"] for x in client.get("/communities?q=anim", headers=a).json()["items"]] == ["anime90"]
    assert client.delete(f"/communities/{c['id']}/join", headers=a).json()["error"] == "owner_stays"
    client.post(f"/communities/{c['id']}/join", headers=b)
    assert client.delete(f"/communities/{c['id']}/join", headers=b).json()["members"] == 1
    assert client.get(f"/communities/{c['id']}", headers=b).json()["joined"] is False


def test_names_are_checked(client, login):
    a = login(1, "ana")
    assert make(client, a, name="Qué mierda de grupo", slug="grupo").json()["name"] == "Qué xxxx de grupo"
    assert make(client, a, name="Porno gratis xxx", slug="nope").status_code == 400
    assert make(client, a, name="ok", slug="admin").status_code == 400
    assert make(client, a, name="x", slug="valid1").status_code == 400  # name of 3 to 40 characters


def test_the_owner_moderates_their_community(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    c = make(client, a).json()
    client.post(f"/communities/{c['id']}/join", headers=b)
    p = client.post("/posts", json={"text": "spam", "communityId": c["id"]}, headers=b).json()
    assert client.delete(f"/posts/{p['id']}", headers=a).status_code == 200
    assert client.get(f"/communities/{c['id']}/feed", headers=a).json()["items"] == []


def test_community_notices_are_few_and_grouped(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    d = login(3, "dani")
    c = make(client, a).json()
    client.post(f"/communities/{c['id']}/join", headers=b)
    client.post(f"/communities/{c['id']}/join", headers=d)
    joins = [n for n in client.get("/notifications", headers=a).json()["items"] if n["kind"] == "community_join"]
    assert len(joins) == 1 and joins[0]["count"] == 2 and joins[0]["community"]["name"] == "Cine de terror"
    client.post("/posts", json={"text": "uno", "communityId": c["id"]}, headers=b)
    second = client.post("/posts", json={"text": "dos", "communityId": c["id"]}, headers=b).json()
    posts = [n for n in client.get("/notifications", headers=d).json()["items"] if n["kind"] == "community_post"]
    assert len(posts) == 1 and posts[0]["count"] == 2 and posts[0]["postId"] == second["id"]
    assert posts[0]["community"] == {"id": c["id"], "name": "Cine de terror", "slug": "terror"}
    # The author hears nothing of their own post, and a community post is not a "new post" for followers.
    assert [n for n in client.get("/notifications", headers=b).json()["items"] if n["kind"].endswith("post")] == []
