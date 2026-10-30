"""Link previews: the person's device reads the page and sends what it says; the server only checks it and keeps it."""
from app import preview

URL = "https://letterboxd.com/film/dune-part-two/"
GOOD = {"url": URL, "title": "Dune: Part Two (2024)", "desc": "Follow the mythic journey", "site": "Letterboxd",
        "image": "https://a.ltrbxd.com/resized/film-poster/dune.jpg"}


def post(client, headers, text, **extra):
    r = client.post("/posts", json={"text": text, **extra}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_first_url():
    assert preview.first_url("mira https://a.example/x?y=1). ok") == "https://a.example/x?y=1"
    assert preview.first_url("nada") is None


def test_the_device_preview_is_kept_as_sent(client, login):
    link = post(client, login(1), "Para el finde " + URL, link=GOOD)["link"]
    assert link == GOOD


def test_a_preview_for_another_link_is_ignored(client, login):
    link = post(client, login(1), "Para el finde " + URL, link={**GOOD, "url": "https://evil.example/"})["link"]
    assert link == {"url": URL}
    assert "link" not in post(client, login(1), "sin link", link=GOOD)


def test_the_preview_is_checked(client, login):
    a = login(1)
    link = post(client, a, URL, link={**GOOD, "title": "Qué mierda de final " + "x" * 300, "image": "http://plain.example/a.jpg",
                                      "desc": 42, "site": "s" * 200})["link"]
    assert link["title"].startswith("Qué xxxx de final") and len(link["title"]) <= 120
    assert "image" not in link and "desc" not in link and len(link["site"]) <= 60
    assert "image" not in post(client, a, URL, link={**GOOD, "image": "javascript:alert(1)"})["link"]
    assert post(client, a, URL, link={**GOOD, "title": ""})["link"] == {"url": URL}


def test_adult_previews_are_dropped(client, login):
    a = login(1)
    assert post(client, a, "https://www.pornhub.com/view", link={**GOOD, "url": "https://www.pornhub.com/view"})["link"] == \
        {"url": "https://www.pornhub.com/view"}
    assert post(client, a, URL, link={**GOOD, "title": "Videos porno gratis xxx"})["link"] == {"url": URL}
    assert "image" not in post(client, a, URL, link={**GOOD, "image": "https://cdn.xvideos.com/p.jpg"})["link"]


def test_a_tmdb_or_imdb_link_takes_the_title_kino_found(client, login):
    hint = {"kind": "movie", "tmdbId": 693134, "title": "Dune: Parte dos", "year": 2024, "poster": "https://image.tmdb.org/t/p/w342/dune.jpg"}
    a = login(1)
    link = post(client, a, "Brutal https://www.imdb.com/title/tt15239678/", title=hint)["link"]
    assert link == {"url": "https://www.imdb.com/title/tt15239678/", "title": "Dune: Parte dos", "desc": "2024", "site": "TMDB",
                    "image": "https://image.tmdb.org/t/p/w342/dune.jpg", "tmdb": {"kind": "movie", "id": 693134}}
    assert "tmdb" not in post(client, a, "https://blog.example/x", title=hint)["link"]
    bad = post(client, a, "https://www.themoviedb.org/movie/693134", title={**hint, "poster": "https://evil.example/p.jpg"})["link"]
    assert bad["tmdb"] == {"kind": "movie", "id": 693134} and "image" not in bad


# ---- a title attached in the composer, without a link in the text ----------------------------------------------------

DUNE = {"kind": "movie", "tmdbId": 693134, "title": "Dune: Parte dos", "year": 2024, "poster": "https://image.tmdb.org/t/p/w342/dune.jpg"}


def test_an_attached_title_alone_makes_the_card(client, login):
    a = login(1)
    out = post(client, a, "La vi anoche.\nBrutal el final.\n\nQué sonido", title=DUNE)
    assert out["text"] == "La vi anoche.\nBrutal el final.\n\nQué sonido"  # the text is never touched, line breaks kept
    assert out["link"] == {"url": "https://www.themoviedb.org/movie/693134", "title": "Dune: Parte dos", "desc": "2024",
                           "site": "TMDB", "image": "https://image.tmdb.org/t/p/w342/dune.jpg", "tmdb": {"kind": "movie", "id": 693134}}
    series = post(client, a, "Empezando", title={"kind": "series", "tmdbId": 1396, "title": "Breaking Bad"})["link"]
    assert series == {"url": "https://www.themoviedb.org/tv/1396", "title": "Breaking Bad", "site": "TMDB",
                      "tmdb": {"kind": "series", "id": 1396}}
    # The feed and the post itself carry the same card.
    feed = client.get("/feed", headers=a).json()["items"]
    assert next(i for i in feed if i["type"] == "post" and i["id"] == out["id"])["link"] == out["link"]
    assert client.get(f"/posts/{out['id']}", headers=a).json()["link"] == out["link"]


def test_an_attached_title_alone_may_go_without_text(client, login):
    a = login(1)
    out = post(client, a, "   ", title=DUNE)
    assert out["text"] == "" and out["link"]["tmdb"] == {"kind": "movie", "id": 693134}
    # Without a usable title, an empty text is still refused.
    assert client.post("/posts", json={"text": ""}, headers=a).status_code == 400
    assert client.post("/posts", json={"text": " ", "title": {**DUNE, "kind": "person"}}, headers=a).status_code == 400


def test_a_bad_attached_title_is_dropped(client, login):
    for bad in ({**DUNE, "kind": "person"}, {**DUNE, "kind": "tv"}, {**DUNE, "tmdbId": 0}, {**DUNE, "tmdbId": 10**9},
                {**DUNE, "tmdbId": "693134"}, {**DUNE, "tmdbId": True}, {**DUNE, "tmdbId": 1.5}, {**DUNE, "title": ""},
                {**DUNE, "title": "Videos porno gratis xxx"}, "Dune", [DUNE], None):
        assert preview.link_for("Hola", None, bad) == {}, bad
    for poster in ("https://evil.example/t/p/w342/dune.jpg", "https://image.tmdb.org.evil.example/p.jpg",
                   "http://image.tmdb.org/t/p/w342/dune.jpg", "javascript:alert(1)", 42):
        card = preview.link_for("Hola", None, {**DUNE, "poster": poster})
        assert "image" not in card and card["tmdb"] == {"kind": "movie", "id": 693134}, poster
    assert "desc" not in preview.link_for("Hola", None, {**DUNE, "year": 3000})
    # Over HTTP: the post is kept, without a card, its text as written.
    out = post(client, login(1), "Hola", title={**DUNE, "title": "Videos porno gratis xxx"})
    assert "link" not in out and out["text"] == "Hola"


def test_an_attached_title_is_cleaned_and_masked(client, login):
    link = post(client, login(1), "Hola", title={**DUNE, "title": "  <b>Qué  mierda</b>\n" + "x" * 300})["link"]
    assert link["title"].startswith("Qué xxxx") and "<" not in link["title"] and len(link["title"]) <= 120


def test_with_a_link_in_the_text_the_link_wins(client, login):
    a = login(1)
    other = {"kind": "series", "tmdbId": 1396, "title": "Breaking Bad"}
    # A TMDB link: the hint describes it, as before (the composer sends the link's own title then).
    assert post(client, a, "https://www.themoviedb.org/movie/693134", title=DUNE)["link"]["url"] == "https://www.themoviedb.org/movie/693134"
    # Any other link: its own preview (or none), never the attached title.
    link = post(client, a, "Mira " + URL, link=GOOD, title=other)["link"]
    assert link == GOOD
    assert post(client, a, "Mira https://blog.example/x", title=other)["link"] == {"url": "https://blog.example/x"}
