"""Link previews (owner, 2026-10-29: the client does the work, to keep the server light). The person's device reads the
page's head (or YouTube's oEmbed, or Kino's TMDB for a TMDB or IMDb link) and sends what it found with the post; the server
never fetches anything. It only checks what came: the preview must describe the post's own first link, its text is cut,
cleaned and masked, its picture must be an https address, and anything 18+ is dropped. The picture is loaded later by each
viewer's device from where it lives."""
import re
from html import unescape
from urllib.parse import urlsplit

from . import profanity

URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TRAILING = ".,;:!?)]}»\"'"
ADULT_DOMAINS = {"pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com", "onlyfans.com", "redtube.com",
                 "youporn.com", "chaturbate.com"}
TITLE_HOSTS = {"themoviedb.org", "imdb.com"}
TMDB_IMAGES = "https://image.tmdb.org/t/p/"
IMAGE = re.compile(r"^https://[^/\s]+\.[^/\s]+\S*$")


def first_url(text: str) -> str | None:
    m = URL.search(text)
    if not m:
        return None
    url = m.group(0).rstrip(TRAILING)
    return url if len(url) <= 2048 else None


def _host_in(host: str, hosts: set[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in hosts)


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _clean(text, limit: int) -> str | None:
    if not isinstance(text, str):
        return None
    text = re.sub(r"<[^>]*>", " ", unescape(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or None


def _image(url) -> str | None:
    if not isinstance(url, str) or len(url) > 1000 or not IMAGE.match(url) or _host_in(_host(url), ADULT_DOMAINS):
        return None
    return url


def _from_device(url: str, sent) -> dict | None:
    """What the device read for [url], checked; None when it is not about this link, has no title or looks 18+."""
    if not isinstance(sent, dict) or sent.get("url") != url:
        return None
    title, desc, site = _clean(sent.get("title"), 120), _clean(sent.get("desc"), 200), _clean(sent.get("site"), 60)
    if not title or any(profanity.looks_adult(t) for t in (title, desc, site)):
        return None
    out = {"title": profanity.mask(title), "desc": profanity.mask(desc) if desc else None, "site": site or _host(url)[:60]}
    image = _image(sent.get("image"))
    if image:
        out["image"] = image
    return out


def _from_tmdb(url: str, hint) -> dict | None:
    """The title Kino's TMDB found for a TMDB or IMDb link (those sites refuse previews): checked, never trusted."""
    if not isinstance(hint, dict) or not _host_in(_host(url), TITLE_HOSTS):
        return None
    kind, tmdb_id = hint.get("kind"), hint.get("tmdbId")
    if kind not in ("movie", "series") or not isinstance(tmdb_id, int) or isinstance(tmdb_id, bool) or not 0 < tmdb_id < 10**9:
        return None
    title = _clean(hint.get("title"), 120)
    if not title or profanity.looks_adult(title):
        return None
    year = hint.get("year")
    out = {"title": profanity.mask(title), "desc": str(year) if isinstance(year, int) and 1870 < year < 2100 else None,
           "site": "TMDB", "tmdb": {"kind": kind, "id": tmdb_id}}
    poster = hint.get("poster")
    if isinstance(poster, str) and poster.startswith(TMDB_IMAGES) and _image(poster):
        out["image"] = poster
    return out


def link_for(text: str, sent=None, hint=None) -> dict:
    """{} without a link; {"url"} when there is no usable preview; else url, title, desc, site, image and tmdb as checked."""
    url = first_url(text)
    if url is None:
        return {}
    if _host_in(_host(url), ADULT_DOMAINS):
        return {"url": url}
    found = _from_tmdb(url, hint) or _from_device(url, sent)
    if not found:
        return {"url": url}
    return {"url": url, **{k: v for k, v in found.items() if v is not None}}
