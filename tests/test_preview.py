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
