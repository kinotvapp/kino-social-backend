"""Creator profiles (owner, 2026-10-29): a plugin author's GitHub username, and the kino-plugin repos they publish.

The badge and this list show on a profile whenever `users.github_username` is set (`social.py` reads and writes it: no
separate flag). `GET /users/<id>/plugins` answers from `creator_plugins`, re-fetched from GitHub's search API when the
cached row is older than [CACHE_MS]; a failed fetch (GitHub down, rate-limited) falls back to the last cached list, or an
empty one on a first-ever fetch — never a 500 that would break the profile page. [fetch] is replaceable
(`app.state.github_fetch`) so tests never touch the real network.

The `creator_plugins` row IS the cache -- this endpoint does not also go through the scoped `ReadCache` (`cache.py`):
that one assumes a cached response is good until something bumps its scope, which does not fit "retry the next request
after a failed fetch" (an empty or stale response would otherwise stick for the whole TTL).
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from . import db
from .main import current_user

router = APIRouter()

CACHE_MS = 6 * 3600 * 1000
MAX_PLUGINS = 100


def _parse(items) -> list[dict]:
    """GitHub search hits -> our shape, lowercased `repo`, an item without a plain `owner/name` dropped. Pure (no
    network), so this is unit-tested on its own; [fetch] is the only caller that sees a real GitHub response."""
    out = []
    for item in (items or [])[:MAX_PLUGINS]:
        full_name = item.get("full_name")
        if not isinstance(full_name, str) or "/" not in full_name:
            continue
        out.append({
            "repo": full_name.lower(),
            "name": (item.get("name") or full_name.split("/")[-1])[:60],
            "description": (item.get("description") or "")[:300],
            "stars": item.get("stargazers_count") if isinstance(item.get("stargazers_count"), int) else 0,
        })
    return out


def fetch(username: str) -> list[dict]:
    """GitHub's search API: every public repo with the topic `kino-plugin` that [username] owns, newest stars first.
    Raises on any failure (network, rate limit, a bad response) -- the caller falls back to what is cached."""
    import os
    import urllib.request

    url = f"https://api.github.com/search/repositories?q=user:{username}+topic:kino-plugin&sort=stars&per_page={MAX_PLUGINS}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "kino-social-backend"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as r:
        data = json.load(r)
    return _parse(data.get("items"))


@router.get("/users/{user_id}/plugins")
def plugins_of(user_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    target = conn.execute("SELECT github_username FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None or not target["github_username"]:
        raise HTTPException(404, "not_found")
    username = target["github_username"]
    key = username.lower()
    getter = getattr(request.app.state, "github_fetch", fetch)

    row = conn.execute("SELECT fetched_at, plugins FROM creator_plugins WHERE github_username = ?", (key,)).fetchone()
    now = db.now_ms()
    if row is not None and now - row["fetched_at"] < CACHE_MS:
        return {"plugins": json.loads(row["plugins"])}
    try:
        found = getter(username)
    except Exception:
        return {"plugins": json.loads(row["plugins"]) if row is not None else []}
    conn.execute(
        "INSERT INTO creator_plugins (github_username, fetched_at, plugins) VALUES (?, ?, ?)"
        " ON CONFLICT (github_username) DO UPDATE SET fetched_at = EXCLUDED.fetched_at, plugins = EXCLUDED.plugins",
        (key, now, json.dumps(found)))
    return {"plugins": found}
