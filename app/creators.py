"""Creator profiles (owner, 2026-10-30): which Kino Social handle a kino-plugin repo names as its author -- a claim
the PLUGIN's own manifest makes (`kino-plugin.json`'s `creator` field, CREATOR-PROFILE-SPEC.md §1a), never one a
person types about themselves. `refresh()` keeps `creator_plugins` current by pulling kino-plugin-store's already
public daily index (§1b) -- no new cross-repo secret, no new endpoint there, just a GET of a public file. A person's
only write here is hiding or unhiding one of their own rows (§1c); `GET /users/<id>/plugins` and `GET /me/plugins`
(§3) are plain reads of the table.

[refresh] is meant to run on a timer, never per request -- see `main.create_app`'s background thread. [fetch] is
replaceable (`app.state.index_fetch`) so tests never touch the real network.
"""
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request

from .main import current_user

log = logging.getLogger("kino_social")
router = APIRouter()

INDEX_URL = "https://raw.githubusercontent.com/kinotvapp/kino-plugin-store/HEAD/index.json"
# The same shape `kino-plugin-store`'s own `entries.js` checks before it ever writes `creator` into the index (and
# the same one `_check_handle` enforces on the way in, lowercased since every handle is stored lowercase).
KINO_USER = re.compile(r"^[a-z0-9_]{3,20}$")


def _parse(index) -> list[dict]:
    """The index's entries with a usable `creator` -> our row shape (lowercased `kino_user`, `repo`). Pure (no
    network), so this is unit-tested on its own; [fetch_index] is the only caller that sees a real GitHub response."""
    out = []
    entries = index.get("entries") if isinstance(index, dict) else None
    for e in (entries or []):
        if not isinstance(e, dict):
            continue
        creator, address = e.get("creator"), e.get("address")
        if not isinstance(creator, str) or not isinstance(address, str) or "/" not in address:
            continue
        kino_user = creator.strip().lower()
        if not KINO_USER.match(kino_user):
            continue
        out.append({
            "kino_user": kino_user,
            "repo": address.lower(),
            "name": (e.get("name") or address.split("/")[-1])[:60],
            "description": (e.get("description") or "")[:300],
            "stars": e["popularity"] if isinstance(e.get("popularity"), int) else 0,
        })
    return out


def fetch_index() -> dict:
    """kino-plugin-store's public daily index, straight from GitHub -- the same file Kino's own "Kino Plugins" store
    reads. Raises on any failure (network, a bad response); [refresh] just skips that run when this does."""
    import urllib.request

    headers = {"Accept": "application/json", "User-Agent": "kino-social-backend"}
    with urllib.request.urlopen(urllib.request.Request(INDEX_URL, headers=headers), timeout=10) as r:
        return json.load(r)


def refresh(conn, fetch=fetch_index) -> int:
    """Replaces `creator_plugins` with what the index says today, preserving every `hidden` flag already set on a
    `(kino_user, repo)` pair that is still there (§1b). Returns the row count written, or -1 when the fetch itself
    failed -- raises nothing, so one bad run (network, GitHub down, a malformed body) never empties anyone's list."""
    try:
        found = _parse(fetch())
    except Exception:
        log.exception("creator-plugins refresh: could not fetch the index, keeping what is there")
        return -1
    keys = {(r["kino_user"], r["repo"]) for r in found}
    with conn.transaction() as tx:
        existing = {(r["kino_user"], r["repo"]) for r in tx.execute("SELECT kino_user, repo FROM creator_plugins").fetchall()}
        for kino_user, repo in existing - keys:
            tx.execute("DELETE FROM creator_plugins WHERE kino_user = ? AND repo = ?", (kino_user, repo))
        for r in found:
            tx.execute(
                "INSERT INTO creator_plugins (kino_user, repo, name, description, stars, hidden)"
                " VALUES (?, ?, ?, ?, ?, FALSE)"
                " ON CONFLICT (kino_user, repo) DO UPDATE SET"
                " name = EXCLUDED.name, description = EXCLUDED.description, stars = EXCLUDED.stars",
                (r["kino_user"], r["repo"], r["name"], r["description"], r["stars"]))
    return len(found)


def _row_json(r, *, include_hidden: bool = False) -> dict:
    out = {"repo": r["repo"], "name": r["name"], "description": r["description"], "stars": r["stars"]}
    if include_hidden and r["hidden"]:
        out["hidden"] = True
    return out


@router.get("/users/{user_id}/plugins")
def plugins_of(user_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    target = conn.execute("SELECT handle FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None or not target["handle"]:
        return {"plugins": []}  # no handle yet (still mid-signup): same as having none, never a 404
    rows = conn.execute(
        "SELECT repo, name, description, stars FROM creator_plugins WHERE kino_user = ? AND NOT hidden"
        " ORDER BY stars DESC, repo",
        (target["handle"],)).fetchall()
    return {"plugins": [_row_json(r) for r in rows]}


@router.get("/me/plugins")
def my_plugins(request: Request, all: bool = False, user=Depends(current_user)):
    if all:
        rows = request.app.state.db.execute(
            "SELECT repo, name, description, stars, hidden FROM creator_plugins WHERE kino_user = ?"
            " ORDER BY stars DESC, repo",
            (user["handle"],)).fetchall()
        return {"plugins": [_row_json(r, include_hidden=True) for r in rows]}
    rows = request.app.state.db.execute(
        "SELECT repo, name, description, stars FROM creator_plugins WHERE kino_user = ? AND NOT hidden"
        " ORDER BY stars DESC, repo",
        (user["handle"],)).fetchall()
    return {"plugins": [_row_json(r) for r in rows]}


def _set_hidden(request: Request, user, repo: str, hidden: bool) -> dict:
    conn = request.app.state.db
    result = conn.execute(
        "UPDATE creator_plugins SET hidden = ? WHERE kino_user = ? AND repo = ?",
        (hidden, user["handle"], repo.lower()))
    if result.rowcount == 0:
        raise HTTPException(404, "not_found")  # never theirs -- checked by kino_user, not who owns the GitHub repo
    return {"ok": True}


@router.post("/me/plugins/{repo:path}/hide")
def hide_plugin(repo: str, request: Request, user=Depends(current_user)):
    return _set_hidden(request, user, repo, True)


@router.post("/me/plugins/{repo:path}/unhide")
def unhide_plugin(repo: str, request: Request, user=Depends(current_user)):
    return _set_hidden(request, user, repo, False)
