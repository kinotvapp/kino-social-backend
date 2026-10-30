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


def test_a_private_community_shows_its_name_but_its_posts_only_to_members(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    r = client.post("/communities", json={"name": "Club privado", "slug": "club", "description": "Solo invitados", "private": True}, headers=a)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["private"] is True and c["requested"] is False and c["pending"] == 0
    # Listed and searchable by name, so people can ask to join.
    assert [x["slug"] for x in client.get("/communities?q=club", headers=b).json()["items"]] == ["club"]
    seen = client.get(f"/communities/{c['id']}", headers=b).json()
    assert seen["name"] == "Club privado" and seen["private"] is True and "pending" not in seen
    p = client.post("/posts", json={"text": "solo para el club", "communityId": c["id"]}, headers=a).json()
    # Its feed, its posts, their comments and likes stay closed to a non-member.
    assert client.get(f"/communities/{c['id']}/feed", headers=b).json()["error"] == "private"
    assert client.get(f"/posts/{p['id']}", headers=b).status_code == 403
    assert client.get(f"/posts/{p['id']}/comments", headers=b).status_code == 403
    assert client.post(f"/posts/{p['id']}/comments", json={"text": "hola"}, headers=b).status_code == 403
    # Nor in its author's profile; the author still sees it there.
    ana = client.get("/me", headers=a).json()["id"]
    assert client.get(f"/users/{ana}", headers=b).json()["posts"] == []
    assert [x["id"] for x in client.get(f"/users/{ana}", headers=a).json()["posts"]] == [p["id"]]
    assert client.post(f"/posts/{p['id']}/like", headers=b).status_code == 403
    assert client.post("/posts", json={"text": "hola", "communityId": c["id"]}, headers=b).status_code == 403
    # A public community stays open as before.
    assert make(client, a, slug="abierta").json()["private"] is False


def test_joining_a_private_community_is_a_request_the_owner_approves(client, login):
    a = login(1, "ana")
    b = login(2, "beto")
    d = login(3, "dani")
    c = client.post("/communities", json={"name": "Club privado", "slug": "club", "private": True}, headers=a).json()
    asked = client.post(f"/communities/{c['id']}/join", headers=b).json()
    assert asked["joined"] is False and asked["requested"] is True and asked["members"] == 1
    client.post(f"/communities/{c['id']}/join", headers=d)
    # The owner hears of it (one grouped notice) and sees who is waiting; nobody else may look.
    reqs = [n for n in client.get("/notifications", headers=a).json()["items"] if n["kind"] == "community_request"]
    assert len(reqs) == 1 and reqs[0]["count"] == 2 and reqs[0]["community"]["slug"] == "club"
    assert client.get(f"/communities/{c['id']}", headers=a).json()["pending"] == 2
    waiting = client.get(f"/communities/{c['id']}/requests", headers=a).json()["items"]
    assert [w["name"] for w in waiting] == ["beto", "dani"]
    assert client.get(f"/communities/{c['id']}/requests", headers=b).status_code == 403
    # Approve one, reject the other.
    ok = client.post(f"/communities/{c['id']}/requests/{waiting[0]['id']}", json={"approve": True}, headers=a).json()
    assert ok["pending"] == 1 and ok["members"] == 2
    client.post(f"/communities/{c['id']}/requests/{waiting[1]['id']}", json={"approve": False}, headers=a)
    assert client.get(f"/communities/{c['id']}/requests", headers=a).json()["items"] == []
    assert client.get(f"/communities/{c['id']}", headers=b).json()["joined"] is True
    assert client.get(f"/communities/{c['id']}/feed", headers=b).status_code == 200
    assert client.get(f"/communities/{c['id']}", headers=d).json()["requested"] is False
    assert client.get(f"/communities/{c['id']}/feed", headers=d).status_code == 403
    # The approved person hears of it; the rejected one is not told.
    assert [n["kind"] for n in client.get("/notifications", headers=b).json()["items"]] == ["community_approved"]
    assert client.get("/notifications", headers=d).json()["items"] == []
    # Asking again, or cancelling: a request is withdrawn with the same call that leaves.
    client.post(f"/communities/{c['id']}/join", headers=d)
    assert client.delete(f"/communities/{c['id']}/join", headers=d).json()["requested"] is False
    assert client.get(f"/communities/{c['id']}/requests", headers=a).json()["items"] == []
    # An answer to someone who never asked does nothing.
    assert client.post(f"/communities/{c['id']}/requests/{waiting[1]['id']}", json={"approve": True}, headers=a).status_code == 404
