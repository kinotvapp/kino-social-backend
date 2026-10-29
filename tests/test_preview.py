import re

import httpx
import pytest

from app import preview

PUBLIC_IP = "93.184.216.34"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

PAGE = """<html><head>
<title>Fallback title</title>
<meta property="og:title" content="Big Buck &amp; Bunny">
<meta property="og:description" content="A   free
film from 2008">
<meta property="og:site_name" content="Blender">
<meta property="og:image" content="/img/poster.png">
</head><body>hi</body></html>"""


def resolver_for(table):
    def resolve(host, *args, **kwargs):
        ip = table.get(host, PUBLIC_IP)
        return [(2, 1, 6, "", (ip, 443))]

    return resolve


def handler_for(routes):
    def handle(request: httpx.Request):
        key = str(request.url)
        if key not in routes:
            return httpx.Response(404)
        status, headers, body = routes[key]
        return httpx.Response(status, headers=headers, content=body)

    return handle


def make_fetcher(routes, ips=None):
    return preview.Fetcher(transport=httpx.MockTransport(handler_for(routes)), resolver=resolver_for(ips or {}),
                           public_url="https://social.example")


@pytest.fixture
def fetcher(request):
    return getattr(request, "param", None)


def post(client, login, text):
    return client.post("/posts", json={"text": text}, headers=login(1)).json()


@pytest.mark.parametrize("fetcher", [make_fetcher({
    "https://blender.example/bbb": (200, {"content-type": "text/html; charset=utf-8"}, PAGE.encode()),
    "https://blender.example/img/poster.png": (200, {"content-type": "image/png"}, PNG),
})], indirect=True)
def test_preview_reads_open_graph(client, login):
    item = post(client, login, "Mira esto https://blender.example/bbb.")
    link = item["link"]
    assert link["url"] == "https://blender.example/bbb"
    assert link["title"] == "Big Buck & Bunny"
    assert link["desc"] == "A free film from 2008"
    assert link["site"] == "Blender"
    assert re.fullmatch(r"https://social\.example/media/[0-9a-f]{32}", link["image"])
    media = client.get("/media/" + link["image"].rsplit("/", 1)[1])
    assert media.status_code == 200 and media.content == PNG and media.headers["content-type"] == "image/png"


@pytest.mark.parametrize("fetcher", [make_fetcher({
    "https://plain.example/": (200, {"content-type": "text/html"}, b"<title> Just  a title </title>"),
})], indirect=True)
def test_preview_falls_back_to_title_tag(client, login):
    link = post(client, login, "https://plain.example/")["link"]
    assert link["title"] == "Just a title" and link["site"] == "plain.example" and "image" not in link


@pytest.mark.parametrize("fetcher", [make_fetcher({
    "http://127.0.0.1/": (200, {"content-type": "text/html"}, PAGE.encode()),
    "http://192.168.2.10/": (200, {"content-type": "text/html"}, PAGE.encode()),
    "http://localhost/": (200, {"content-type": "text/html"}, PAGE.encode()),
    "https://inside.example/": (200, {"content-type": "text/html"}, PAGE.encode()),
    "https://bounce.example/": (302, {"location": "http://169.254.169.254/latest"}, b""),
    "http://169.254.169.254/latest": (200, {"content-type": "text/html"}, PAGE.encode()),
}, ips={"inside.example": "10.0.0.5", "localhost": "127.0.0.1"})], indirect=True)
def test_preview_refuses_private_targets(client, login):
    for url in ["http://127.0.0.1/", "http://192.168.2.10/", "http://localhost/", "https://inside.example/",
                "https://bounce.example/"]:
        link = post(client, login, "ver " + url)["link"]
        assert link == {"url": url}, url


@pytest.mark.parametrize("fetcher", [make_fetcher({
    "https://www.xvideos.com/x": (200, {"content-type": "text/html"}, PAGE.encode()),
    "https://videos.example/": (200, {"content-type": "text/html"}, b'<meta property="og:title" content="Videos porno gratis">'),
})], indirect=True)
def test_adult_preview_dropped(client, login):
    assert post(client, login, "https://www.xvideos.com/x")["link"] == {"url": "https://www.xvideos.com/x"}
    assert post(client, login, "https://videos.example/")["link"] == {"url": "https://videos.example/"}


def _boom(request):
    raise httpx.ConnectError("down")


@pytest.mark.parametrize("fetcher", [preview.Fetcher(transport=httpx.MockTransport(_boom), resolver=resolver_for({}),
                                                     public_url="https://social.example")], indirect=True)
def test_preview_failure_keeps_post(client, login):
    item = post(client, login, "roto https://down.example/page")
    assert item["text"] == "roto https://down.example/page" and item["link"] == {"url": "https://down.example/page"}


def test_first_url():
    assert preview.first_url("hola https://a.example/x?y=1). y más") == "https://a.example/x?y=1"
    assert preview.first_url("sin enlace") is None
    assert preview.first_url("ftp://a.example") is None


LATIN = "<html><head><meta charset=\"iso-8859-1\"><meta property=\"og:title\" content=\"Última hora en Bogotá\"></head></html>"


@pytest.mark.parametrize("fetcher", [make_fetcher({
    "https://news.example/": (200, {"content-type": "text/html"}, LATIN.encode("latin-1")),
})], indirect=True)
def test_preview_honours_the_page_charset(client, login):
    assert post(client, login, "https://news.example/")["link"]["title"] == "Última hora en Bogotá"


YT = "https://www.youtube.com/watch?v=U2Qp5pL3ovA"
OEMBED = str(httpx.URL("https://www.youtube.com/oembed", params={"url": YT, "format": "json"}))


@pytest.mark.parametrize("fetcher", [make_fetcher({
    OEMBED: (200, {"content-type": "application/json"},
             b'{"title": "Dune: Part Two | Official Trailer", "author_name": "Warner Bros.", "thumbnail_url": "https://i.ytimg.com/vi/x/hqdefault.jpg"}'),
    "https://i.ytimg.com/vi/x/hqdefault.jpg": (200, {"content-type": "image/jpeg"}, b"\xff\xd8jpeg"),
})], indirect=True)
def test_youtube_links_use_its_oembed(client, login):
    link = post(client, login, "Tráiler " + YT)["link"]
    assert link["title"] == "Dune: Part Two | Official Trailer" and link["site"] == "YouTube"
    assert link["desc"] == "Warner Bros." and link["image"].startswith("https://social.example/media/")


@pytest.mark.parametrize("fetcher", [make_fetcher({
    "https://image.tmdb.org/t/p/w342/dune.jpg": (200, {"content-type": "image/jpeg"}, b"\xff\xd8poster"),
})], indirect=True)
def test_a_tmdb_or_imdb_link_takes_the_title_kino_found(client, login):
    hint = {"kind": "movie", "tmdbId": 693134, "title": "Dune: Parte dos", "year": 2024, "poster": "https://image.tmdb.org/t/p/w342/dune.jpg"}
    a = login(1)
    link = client.post("/posts", json={"text": "Brutal https://www.imdb.com/title/tt15239678/", "title": hint}, headers=a).json()["link"]
    assert link["title"] == "Dune: Parte dos" and link["site"] == "TMDB" and link["desc"] == "2024"
    assert link["tmdb"] == {"kind": "movie", "id": 693134} and link["image"].startswith("https://social.example/media/")
    # Never for another site, never a poster from elsewhere.
    other = client.post("/posts", json={"text": "https://blog.example/x", "title": hint}, headers=a).json()["link"]
    assert "tmdb" not in other
    bad = client.post("/posts", json={"text": "https://www.themoviedb.org/movie/693134",
                                      "title": {**hint, "poster": "https://evil.example/p.jpg"}}, headers=a).json()["link"]
    assert bad["tmdb"] == {"kind": "movie", "id": 693134} and "image" not in bad
