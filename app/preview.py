"""Link previews: read a page's Open Graph tags once, at post time, and keep its image here.

The server is on the owner's home network, so a link must never reach a private address: every hop's host is resolved
and each address must be global before connecting. (A DNS answer that changes between the check and the connect could
still slip through; accepted for the experiment.)
"""
import ipaddress
import json
import re
import secrets
import socket
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from . import profanity

URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TRAILING = ".,;:!?)]}»\"'"
MAX_REDIRECTS, TIMEOUT_S, HTML_MAX, IMAGE_MAX = 3, 5.0, 1_000_000, 2_000_000
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ADULT_DOMAINS = {"pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com", "onlyfans.com", "redtube.com",
                 "youporn.com", "chaturbate.com"}
# Who we are, with a way to reach us (what Wikipedia and others ask of a bot); never a browser's name.
USER_AGENT = "KinoSocialPreview/1.0 (+https://social.comparadorinternet.co; link previews for Kino Social)"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
TITLE_HOSTS = {"themoviedb.org", "imdb.com"}
TMDB_IMAGES = "https://image.tmdb.org/t/p/"
CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_-]+)""", re.IGNORECASE)


@dataclass
class Fetcher:
    transport: httpx.BaseTransport | None = None
    resolver: Callable = socket.getaddrinfo
    public_url: str = "https://social.comparadorinternet.co"
    extra: dict = field(default_factory=dict)


def first_url(text: str) -> str | None:
    m = URL.search(text)
    if not m:
        return None
    url = m.group(0).rstrip(TRAILING)
    return url if len(url) <= 2048 else None


def is_public_host(host: str | None, resolver=socket.getaddrinfo) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host.strip("[]")).is_global  # a literal address needs no resolver
    except ValueError:
        pass
    try:
        infos = resolver(host, None)
    except (OSError, UnicodeError):
        return False
    addresses = {info[4][0] for info in infos}
    try:
        return bool(addresses) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses)
    except ValueError:
        return False


def _adult_domain(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in ADULT_DOMAINS)


class _Meta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"]
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _clean(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    text = re.sub(r"<[^>]*>", " ", unescape(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or None


def _get(client: httpx.Client, url: str, fetcher: Fetcher, max_bytes: int):
    """GET following redirects by hand, checking every host. (final_url, content_type, body) or None."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not is_public_host(parts.hostname, fetcher.resolver):
            return None
        with client.stream("GET", url) as r:
            if r.is_redirect:
                location = r.headers.get("location")
                if not location:
                    return None
                url = urljoin(url, location)
                continue
            if r.status_code != 200:
                return None
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    return None
            return url, r.headers.get("content-type", "").split(";")[0].strip().lower(), body, r.charset_encoding
    return None


def _decode(body: bytes, declared: str | None) -> str:
    """The page's text in the charset its header or its <meta> declares, else UTF-8."""
    m = None if declared else CHARSET.search(body[:4096])
    for charset in (declared, m and m.group(1).decode("ascii"), "utf-8"):
        if charset:
            try:
                return body.decode(charset, errors="replace")
            except LookupError:
                continue
    return body.decode("utf-8", errors="replace")


def _host_in(host: str, hosts: set[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in hosts)


def _image(client, url: str, fetcher: Fetcher):
    image = _get(client, url, fetcher, IMAGE_MAX)
    return (image[1], image[2]) if image and image[1] in IMAGE_TYPES and image[2] else None


def _youtube(client, url: str, fetcher: Fetcher) -> dict | None:
    """YouTube's own oEmbed (its official way to describe a video): title, channel and thumbnail."""
    answer = _get(client, str(httpx.URL("https://www.youtube.com/oembed", params={"url": url, "format": "json"})), fetcher, 100_000)
    if answer is None:
        return None
    try:
        data = json.loads(answer[2])
    except ValueError:
        return None
    title = _clean(data.get("title") if isinstance(data, dict) else None, 120)
    if not title:
        return None
    out = {"title": title, "desc": _clean(data.get("author_name"), 200), "site": "YouTube"}
    thumb = data.get("thumbnail_url")
    if isinstance(thumb, str) and thumb.startswith("https://"):
        image = _image(client, thumb, fetcher)
        if image:
            out["image"] = image
    return out


def fetch_preview(url: str, fetcher: Fetcher) -> dict | None:
    host = urlsplit(url).hostname or ""
    if _adult_domain(host):
        return None
    with httpx.Client(transport=fetcher.transport, timeout=TIMEOUT_S, follow_redirects=False,
                      headers={"user-agent": USER_AGENT, "accept-language": "es,en;q=0.8"}) as client:
        if _host_in(host, YOUTUBE_HOSTS):
            return _youtube(client, url, fetcher)
        page = _get(client, url, fetcher, HTML_MAX)
        if page is None or page[1] not in ("text/html", "application/xhtml+xml"):
            return None
        final_url, _, body, charset = page
        parser = _Meta()
        parser.feed(_decode(body, charset))
        m = parser.meta
        title = _clean(m.get("og:title") or m.get("twitter:title") or parser.title, 120)
        if not title:
            return None
        desc = _clean(m.get("og:description") or m.get("twitter:description") or m.get("description"), 200)
        site = _clean(m.get("og:site_name"), 60) or (urlsplit(final_url).hostname or "")[:60]
        if _adult_domain(urlsplit(final_url).hostname or "") or any(profanity.looks_adult(t) for t in (title, desc, site)):
            return None
        out = {"title": title, "desc": desc, "site": site}
        image_url = m.get("og:image") or m.get("twitter:image")
        if image_url:
            image = _image(client, urljoin(final_url, image_url.strip()), fetcher)
            if image:
                out["image"] = image
        return out


def _title_hint(url: str, hint) -> dict | None:
    """What the plugin found in Kino's TMDB for a TMDB or IMDb link (those sites refuse bots): checked, never trusted."""
    if not isinstance(hint, dict) or not _host_in(urlsplit(url).hostname or "", TITLE_HOSTS):
        return None
    kind, tmdb_id = hint.get("kind"), hint.get("tmdbId")
    if kind not in ("movie", "series") or not isinstance(tmdb_id, int) or isinstance(tmdb_id, bool) or not 0 < tmdb_id < 10**9:
        return None
    title = _clean(hint.get("title") if isinstance(hint.get("title"), str) else None, 120)
    if not title or profanity.looks_adult(title):
        return None
    year = hint.get("year")
    poster = hint.get("poster")
    return {"title": profanity.mask(title), "desc": str(year) if isinstance(year, int) and 1870 < year < 2100 else None,
            "site": "TMDB", "kind": kind, "tmdbId": tmdb_id,
            "poster": poster if isinstance(poster, str) and poster.startswith(TMDB_IMAGES) and len(poster) < 300 else None}


def link_for(app, text: str, hint=None) -> dict:
    """{} without a link; {"url"} when there is no usable preview; else url, title, desc, site and image (our URL)."""
    url = first_url(text)
    if url is None:
        return {}
    fetcher = app.state.fetcher
    titled = _title_hint(url, hint)
    if titled is not None:
        found = {k: titled[k] for k in ("title", "desc", "site")}
        if fetcher is not None and titled["poster"]:
            try:
                with httpx.Client(transport=fetcher.transport, timeout=TIMEOUT_S, follow_redirects=False,
                                  headers={"user-agent": USER_AGENT}) as client:
                    image = _image(client, titled["poster"], fetcher)
            except Exception:  # noqa: BLE001
                image = None
            if image:
                found["image"] = image
        found["tmdb"] = {"kind": titled["kind"], "id": titled["tmdbId"]}
    elif fetcher is None:
        return {"url": url}
    else:
        try:
            found = fetch_preview(url, fetcher)
        except Exception:  # noqa: BLE001 - a preview never blocks a post
            found = None
    if not found:
        return {"url": url}
    link = {"url": url, "title": found["title"], "desc": found["desc"], "site": found["site"]}
    if "tmdb" in found:
        link["tmdb"] = found["tmdb"]
    if "image" in found:
        content_type, data = found["image"]
        media_id = secrets.token_hex(16)
        app.state.db.execute("INSERT INTO media (id, content_type, bytes) VALUES (?, ?, ?)", (media_id, content_type, data))
        link["image"] = fetcher.public_url.rstrip("/") + "/media/" + media_id
    return link
