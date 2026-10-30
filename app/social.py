"""The social API: posts, likes, one-level comments, follows, watched activity, profiles and handles.

Reads go through the scoped read cache (cache.py): every answer names the scopes it depends on, every write bumps the
scopes it changes, so a like on one post never throws away another post's comments.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request

from . import db, moderation, notify, profanity
from .main import current_user, user_json

router = APIRouter()

POST_MAX, COMMENT_MAX, NAME_MAX, BIO_MAX = 500, 300, 40, 160
LIMITS = {"post": 10, "comment": 30, "follow": 30, "like": 60, "handle": 10, "activity": 30, "community": 3}
TMDB_IMAGES = "https://image.tmdb.org/"
MAX_RUN = 2  # never more than two feed items in a row from one person while others can be mixed in
FEW_FOLLOWS = 3
HANDLE = re.compile(r"^[a-z0-9_](?:[a-z0-9_]|\.(?!\.)){1,18}[a-z0-9_]$")
RESERVED = {"kino", "kinotv", "kinoapp", "admin", "administrador", "root", "soporte", "support", "help", "ayuda",
            "moderador", "moderator", "staff", "oficial", "official", "system", "sistema", "telegram", "social", "null"}


BLANK_RUN = re.compile(r"\n[ \t]*(?:\n[ \t]*)+\n")


def tidy_lines(text: str) -> str:
    """A post's or comment's line breaks: CR LF and CR as LF, a run of 3 or more line breaks (blank lines in between) as
    2, no blank lines at the end (owner, 2026-10-30: a post cannot be mostly empty lines)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return BLANK_RUN.sub("\n\n", text).strip()


def _raw_text(body: dict, key: str, max_len: int, *, allow_empty: bool = False, lines: bool = False) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise HTTPException(400, "bad_" + key)
    value = tidy_lines(value) if lines else value.strip()
    if (not value and not allow_empty) or len(value) > max_len:
        raise HTTPException(400, "bad_" + key)
    return value


def _text(body: dict, key: str, max_len: int, *, allow_empty: bool = False) -> str:
    return profanity.mask(_raw_text(body, key, max_len, allow_empty=allow_empty))


def _limit(request: Request, user, bucket: str) -> None:
    if not request.app.state.limiter.allow(user["id"], bucket, LIMITS[bucket]):
        raise HTTPException(429, "rate_limited")


def _bump(request: Request, *scopes: str) -> None:
    request.app.state.cache.bump(*scopes)


def _author(row) -> dict:
    return {"id": row["user_id"], "name": row["author_name"], "handle": row["author_handle"]}


def _season_episode(hint: dict, kind: str | None) -> tuple[int | None, int | None]:
    # A series episode the person attached to a post (player-panel comments). Both numbers must be plain ints within the
    # bounds _clean_activity uses; a movie, or any bad number, stores nothing rather than 0/0.
    if kind != "series":
        return None, None
    season, episode = hint.get("season"), hint.get("episode")
    if not isinstance(season, int) or isinstance(season, bool) or not isinstance(episode, int) or isinstance(episode, bool):
        return None, None
    if not 0 <= season < 1000 or not 0 < episode < 10000:
        return None, None
    return season, episode


def _post_json(row) -> dict:
    # coinsAccount: the author's user_key, which IS their account id in the coins service (same HMAC secret), so the plugin
    # can name the recipient of a gift. It reveals nothing a stranger can turn back into the Telegram id.
    author = {**_author(row), "coinsAccount": row["author_key"]}
    item = {"type": "post", "id": row["id"], "user": author, "text": row["text"], "comments": row["comment_count"],
            "likes": row["like_count"], "liked": bool(row["liked"]), "createdAt": row["created_at"]}
    if row["link_url"]:
        link = {"url": row["link_url"]}
        if row["preview_title"]:
            link.update(title=row["preview_title"], site=row["preview_site"])
            if row["preview_desc"]:
                link["desc"] = row["preview_desc"]
        if row["preview_media"]:
            link["image"] = row["preview_media"]
        if row["preview_tmdb"]:
            link["tmdb"] = {"kind": row["preview_kind"], "id": row["preview_tmdb"]}
            if row["preview_season"] is not None:
                link["season"] = row["preview_season"]
            if row["preview_episode"] is not None:
                link["episode"] = row["preview_episode"]
        item["link"] = link
    if row["community_id"]:
        item["community"] = {"id": row["community_id"], "name": row["community_name"], "slug": row["community_slug"]}
    return item


def _activity_json(row, *, with_hidden: bool = False) -> dict:
    item = {"type": "activity", "id": row["id"], "user": _author(row), "kind": row["kind"], "title": row["title"],
            "tmdbId": row["tmdb_id"], "poster": row["poster"], "watchedAt": row["watched_at"], "createdAt": row["created_at"]}
    if row["season"] is not None:
        item["season"] = row["season"]
    if row["episode"] is not None:
        item["episode"] = row["episode"]
    if row["still"]:
        item["still"] = row["still"]
    if row["episode_title"]:
        item["episodeTitle"] = row["episode_title"]
    if with_hidden:
        item["hidden"] = bool(row["hidden"])
    return item


# The viewer's id is the first parameter of every query built on these.
POSTS_SQL = """
SELECT p.*, u.name AS author_name, u.handle AS author_handle, u.user_key AS author_key, (l.user_id IS NOT NULL) AS liked,
       cm.name AS community_name, cm.slug AS community_slug, cm.owner_id AS community_owner
FROM posts p JOIN users u ON u.id = p.user_id LEFT JOIN likes l ON l.post_id = p.id AND l.user_id = ?
LEFT JOIN communities cm ON cm.id = p.community_id
"""
ACTIVITY_SQL = "SELECT a.*, u.name AS author_name, u.handle AS author_handle FROM activity a JOIN users u ON u.id = a.user_id"


def _one_post(conn, viewer: int, post_id: int):
    return conn.execute(POSTS_SQL + " WHERE p.id = ?", (viewer, post_id)).fetchone()


# ---- the person ------------------------------------------------------------------------------------------------------


def _check_handle(value) -> str:
    if not isinstance(value, str):
        raise HTTPException(400, "bad_handle")
    handle = value.strip().lstrip("@").lower()
    if not HANDLE.match(handle) or handle in RESERVED or profanity.mask(handle.replace(".", " ").replace("_", " ")) != handle.replace(".", " ").replace("_", " "):
        raise HTTPException(400, "bad_handle")
    return handle


@router.get("/handles/{value}")
def handle_available(value: str, request: Request, user=Depends(current_user)):
    try:
        handle = _check_handle(value)
    except HTTPException:
        return {"handle": value.strip().lstrip("@").lower()[:40], "available": False}
    row = request.app.state.db.execute("SELECT id FROM users WHERE handle = ?", (handle,)).fetchone()
    return {"handle": handle, "available": row is None or row["id"] == user["id"]}


@router.patch("/me")
def update_me(body: dict, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    if "handle" in body:
        handle = _check_handle(body["handle"])
        if handle != user["handle"]:
            _limit(request, user, "handle")
            taken = conn.execute("SELECT 1 FROM users WHERE handle = ? AND id <> ?", (handle, user["id"])).fetchone()
            if taken:
                raise HTTPException(409, "handle_taken")
            try:
                conn.execute("UPDATE users SET handle = ? WHERE id = ?", (handle, user["id"]))
            except Exception as e:  # a race with another person picking it at the same moment
                if "unique" in str(e).lower():
                    raise HTTPException(409, "handle_taken") from e
                raise
    if "name" in body:
        name = _text(body, "name", NAME_MAX)
        conn.execute("UPDATE users SET name = ?, name_folded = ? WHERE id = ?", (name, profanity.fold(name), user["id"]))
    if "bio" in body:
        conn.execute("UPDATE users SET bio = ? WHERE id = ?", (_text(body, "bio", BIO_MAX, allow_empty=True), user["id"]))
    if "shareActivity" in body:
        if not isinstance(body["shareActivity"], bool):
            raise HTTPException(400, "bad_shareActivity")
        conn.execute("UPDATE users SET share_activity = ? WHERE id = ?", (body["shareActivity"], user["id"]))
    _bump(request, "feed", "people", f"user:{user['id']}")
    return user_json(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())


# ---- posts, likes, comments ------------------------------------------------------------------------------------------


@router.post("/posts")
def create_post(body: dict, request: Request, user=Depends(current_user)):
    from . import preview

    # The text may be empty only when the post carries a title the server accepted (the composer's attachment).
    raw = _raw_text(body, "text", POST_MAX, allow_empty=True, lines=True)
    link = preview.link_for(profanity.mask(raw), body.get("link"), body.get("title"))
    if not raw and not link.get("tmdb"):
        raise HTTPException(400, "bad_text")
    moderation.check(user)
    text = profanity.mask(raw)
    _limit(request, user, "post")

    conn = request.app.state.db
    from . import communities, notify

    community = communities.postable(conn, body.get("communityId"), user["id"])
    tmdb = link.get("tmdb") or {}
    season, episode = _season_episode(body.get("title") or {}, tmdb.get("kind"))
    new_id = conn.execute(
        "INSERT INTO posts (user_id, text, link_url, preview_title, preview_desc, preview_site, preview_media, preview_kind,"
        " preview_tmdb, preview_season, preview_episode, community_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (user["id"], text, link.get("url"), link.get("title"), link.get("desc"), link.get("site"), link.get("image"),
         tmdb.get("kind"), tmdb.get("id"), season, episode, community, db.now_ms()),
    ).fetchone()["id"]
    if community is None:
        told = notify.new_post(conn, user["id"], new_id)
        _bump(request, "feed", "people", f"user:{user['id']}", *(f"notif:{f}" for f in told))
    else:
        told = communities.posted(conn, community, user["id"], new_id)
        _bump(request, "feed", "communities", f"community:{community}", f"user:{user['id']}", *(f"notif:{f}" for f in told))
    out = _post_json(_one_post(conn, user["id"], new_id))
    if moderation.offends(raw):
        out["moderation"] = moderation.strike(conn, user["id"])  # saved masked; a third strike mutes from now on
    return out


def _owned(conn, table: str, row_id: int, user):
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "not_found")
    if row["user_id"] != user["id"]:
        raise HTTPException(403, "not_yours")
    return row


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    row = conn.execute("SELECT p.user_id, p.community_id, c.owner_id FROM posts p LEFT JOIN communities c ON c.id = p.community_id"
                       " WHERE p.id = ?", (post_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "not_found")
    # Its author, or the owner of the community it was posted in (moderation).
    if user["id"] not in (row["user_id"], row["owner_id"]):
        raise HTTPException(403, "not_yours")
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))  # comments, likes, notices cascade
    if row["community_id"]:
        conn.execute("UPDATE communities SET post_count = GREATEST(post_count - 1, 0) WHERE id = ?", (row["community_id"],))
    _bump(request, "feed", "people", f"post:{post_id}", f"user:{row['user_id']}", f"notif:{row['user_id']}",
          *([f"community:{row['community_id']}", "communities"] if row["community_id"] else []))
    return {"ok": True}


def _post_owner(conn, post_id: int, viewer: int) -> int:
    """Who wrote the post, once [viewer] may reach it (a private community's posts are for its members)."""
    from . import communities

    communities.post_readable(conn, post_id, viewer)
    return conn.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()["user_id"]


@router.post("/posts/{post_id}/like")
def like(post_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    owner = _post_owner(conn, post_id, user["id"])
    _limit(request, user, "like")
    with conn.transaction() as tx:
        added = tx.execute("INSERT INTO likes (post_id, user_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                           (post_id, user["id"], db.now_ms())).rowcount
        if added:
            tx.execute("UPDATE posts SET like_count = like_count + 1 WHERE id = ?", (post_id,))
            notify.like(tx, post_id, owner, user["id"])
        count = tx.execute("SELECT like_count FROM posts WHERE id = ?", (post_id,)).fetchone()["like_count"]
    _bump(request, "feed", f"user:{owner}", f"notif:{owner}")
    return {"likes": count, "liked": True}


@router.delete("/posts/{post_id}/like")
def unlike(post_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    owner = _post_owner(conn, post_id, user["id"])
    _limit(request, user, "like")
    with conn.transaction() as tx:
        gone = tx.execute("DELETE FROM likes WHERE post_id = ? AND user_id = ?", (post_id, user["id"])).rowcount
        if gone:
            tx.execute("UPDATE posts SET like_count = GREATEST(like_count - 1, 0) WHERE id = ?", (post_id,))
        count = tx.execute("SELECT like_count FROM posts WHERE id = ?", (post_id,)).fetchone()["like_count"]
    _bump(request, "feed", f"user:{owner}")
    return {"likes": count, "liked": False}


@router.get("/posts/{post_id}")
def get_post(post_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    from . import communities

    communities.post_readable(conn, post_id, user["id"])

    def compute():
        row = _one_post(conn, user["id"], post_id)
        if row is None:
            raise HTTPException(404, "not_found")
        return _post_json(row)

    return request.app.state.cache.respond(request, user["id"], ["feed", f"post:{post_id}"], compute)


@router.get("/posts/{post_id}/comments")
def list_comments(post_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    _post_owner(conn, post_id, user["id"])

    def compute():
        rows = conn.execute(
            "SELECT c.*, u.name AS author_name, u.handle AS author_handle FROM comments c JOIN users u ON u.id = c.user_id"
            " WHERE c.post_id = ? ORDER BY c.created_at, c.id LIMIT 200",
            (post_id,),
        ).fetchall()
        return {"items": [{"id": r["id"], "user": _author(r), "text": r["text"], "createdAt": r["created_at"]} for r in rows]}

    return request.app.state.cache.respond(request, user["id"], [f"post:{post_id}"], compute)


@router.post("/posts/{post_id}/comments")
def add_comment(post_id: int, body: dict, request: Request, user=Depends(current_user)):
    raw = _raw_text(body, "text", COMMENT_MAX, lines=True)
    moderation.check(user)
    text = profanity.mask(raw)
    conn = request.app.state.db
    owner = _post_owner(conn, post_id, user["id"])
    _limit(request, user, "comment")
    now = db.now_ms()
    with conn.transaction() as tx:
        new_id = tx.execute("INSERT INTO comments (post_id, user_id, text, created_at) VALUES (?, ?, ?, ?) RETURNING id",
                            (post_id, user["id"], text, now)).fetchone()["id"]
        tx.execute("UPDATE posts SET comment_count = comment_count + 1 WHERE id = ?", (post_id,))
        notify.comment(tx, post_id, owner, user["id"])
    _bump(request, "feed", f"post:{post_id}", f"user:{owner}", f"notif:{owner}")
    out = {"id": new_id, "user": {"id": user["id"], "name": user["name"], "handle": user["handle"]}, "text": text, "createdAt": now}
    if moderation.offends(raw):
        out["moderation"] = moderation.strike(conn, user["id"])
    return out


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    row = _owned(conn, "comments", comment_id, user)
    with conn.transaction() as tx:
        tx.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        tx.execute("UPDATE posts SET comment_count = GREATEST(comment_count - 1, 0) WHERE id = ?", (row["post_id"],))
    _bump(request, "feed", f"post:{row['post_id']}")
    return {"ok": True}


# ---- the feed --------------------------------------------------------------------------------------------------------


@router.get("/feed")
def feed(request: Request, before: int | None = None, limit: int = 20, user=Depends(current_user)):
    return request.app.state.cache.respond(request, user["id"], ["feed"], lambda: _feed(request, user, before, limit))


def mix(items: list[dict], max_run: int = MAX_RUN) -> list[dict]:
    """The same items, newest first, but never more than [max_run] in a row from one person while someone else's item
    can still come next. Nothing is dropped or moved to another page: only the order inside the page changes."""
    rest = list(items)
    out: list[dict] = []
    while rest:
        run_author = out[-1]["user"]["id"] if out else None
        run = 0
        for it in reversed(out):
            if it["user"]["id"] != run_author:
                break
            run += 1
        pick = 0
        if run >= max_run:
            pick = next((i for i, it in enumerate(rest) if it["user"]["id"] != run_author), 0)
        out.append(rest.pop(pick))
    return out


def _feed(request: Request, user, before: int | None, limit: int) -> dict:
    limit = max(1, min(limit, 50))
    cutoff = before if before is not None else 2**62
    conn = request.app.state.db
    posts = conn.execute(POSTS_SQL + " WHERE p.community_id IS NULL AND p.created_at < ? ORDER BY p.created_at DESC, p.id DESC LIMIT ?",
                         (user["id"], cutoff, limit)).fetchall()
    page = [_post_json(r) for r in posts]
    # The cursor is the oldest item of the page by time, so mixing the order inside it never skips one on the next page.
    next_cursor = min(i["createdAt"] for i in page) if len(page) == limit else None
    items = mix(page)
    if before is None:
        suggestions = _suggestions(conn, user["id"])
        if suggestions:
            items.insert(min(2, len(items)), {"type": "suggestions", "id": 0, "people": suggestions})
    return {"items": items, "next": next_cursor}


def _suggestions(conn, viewer: int) -> list[dict]:
    following = conn.execute("SELECT COUNT(*) FROM follows WHERE follower_id = ?", (viewer,)).fetchone()[0]
    if following >= FEW_FOLLOWS:
        return []
    rows = conn.execute(
        "SELECT u.id, u.name, u.handle, u.bio, COUNT(p.id) AS n FROM users u JOIN posts p ON p.user_id = u.id"
        " WHERE u.id <> ? AND p.created_at > ? AND u.id NOT IN (SELECT followee_id FROM follows WHERE follower_id = ?)"
        " GROUP BY u.id ORDER BY n DESC, MAX(p.created_at) DESC LIMIT 5",
        (viewer, db.now_ms() - 14 * 86_400_000, viewer),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "handle": r["handle"], "bio": r["bio"]} for r in rows]


# ---- what people watched ---------------------------------------------------------------------------------------------


def _clean_activity(item) -> tuple | None:
    if not isinstance(item, dict) or item.get("adult") is True:
        return None
    kind, tmdb, title = item.get("kind"), item.get("tmdbId"), item.get("title")
    if kind not in ("movie", "series") or not isinstance(tmdb, int) or isinstance(tmdb, bool) or not 0 < tmdb < 2**31:
        return None
    if not isinstance(title, str) or not title.strip() or len(title) > 200 or profanity.looks_adult(title):
        return None
    season, episode = item.get("season"), item.get("episode")
    if kind == "movie" or not isinstance(season, int) or not isinstance(episode, int) or not 0 <= season < 1000 or not 0 < episode < 10000:
        season = episode = None
    poster = item.get("poster")
    if not isinstance(poster, str) or not poster.startswith(TMDB_IMAGES) or len(poster) > 300:
        poster = None
    still = item.get("still")
    if season is None or not isinstance(still, str) or not still.startswith(TMDB_IMAGES) or len(still) > 300:
        still = None
    episode_title = item.get("episodeTitle")
    if season is None or not isinstance(episode_title, str) or not episode_title.strip() or len(episode_title) > 200:
        episode_title = None
    else:
        episode_title = profanity.mask(episode_title.strip())
    watched = item.get("watchedAtMs")
    if not isinstance(watched, int) or watched <= 0:
        return None
    return kind, tmdb, title.strip(), season, episode, poster, still, episode_title, watched


@router.post("/activity")
def add_activity(body: dict, request: Request, user=Depends(current_user)):
    items = body.get("items")
    if not isinstance(items, list) or len(items) > 100:
        raise HTTPException(400, "bad_items")
    _limit(request, user, "activity")
    conn = request.app.state.db
    added = 0
    now = db.now_ms()
    for i, item in enumerate(items):
        clean = _clean_activity(item)
        if clean is None:
            continue
        added += conn.execute(
            "INSERT INTO activity (user_id, kind, tmdb_id, title, season, episode, poster, still, episode_title, watched_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (user["id"], *clean, now + i),  # +i: a batch keeps its order on the profile
        ).rowcount
    if added:
        _bump(request, f"user:{user['id']}")
    return {"added": added}


@router.get("/me/activity")
def my_activity(request: Request, user=Depends(current_user)):
    rows = request.app.state.db.execute(
        ACTIVITY_SQL + " WHERE a.user_id = ? ORDER BY a.watched_at DESC, a.id DESC LIMIT 100", (user["id"],)).fetchall()
    return {"items": [_activity_json(r, with_hidden=True) for r in rows]}


@router.patch("/me/activity/{activity_id}")
def hide_activity(activity_id: int, body: dict, request: Request, user=Depends(current_user)):
    if not isinstance(body.get("hidden"), bool):
        raise HTTPException(400, "bad_hidden")
    conn = request.app.state.db
    _owned(conn, "activity", activity_id, user)
    conn.execute("UPDATE activity SET hidden = ? WHERE id = ?", (body["hidden"], activity_id))
    _bump(request, f"user:{user['id']}")
    return {"ok": True}


# ---- people ----------------------------------------------------------------------------------------------------------


def _is_following(conn, follower: int, followee: int) -> bool:
    return conn.execute("SELECT 1 FROM follows WHERE follower_id = ? AND followee_id = ?", (follower, followee)).fetchone() is not None


@router.get("/users/{user_id}")
def profile(user_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db

    def compute():
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise HTTPException(404, "not_found")
        own = user_id == user["id"]
        activity = []
        # What someone watched is for the people who follow them (spec), never a stranger who opened the profile.
        if own or (target["share_activity"] and conn.execute(
                "SELECT 1 FROM follows WHERE follower_id = ? AND followee_id = ?", (user["id"], user_id)).fetchone()):
            activity = conn.execute(
                ACTIVITY_SQL + " WHERE a.user_id = ? AND NOT a.hidden ORDER BY a.watched_at DESC, a.id DESC LIMIT 50",
                (user_id,)).fetchall()
        from . import communities

        posts = conn.execute(POSTS_SQL + " WHERE p.user_id = ?" + communities.VISIBLE_POSTS + " ORDER BY p.created_at DESC LIMIT 20",
                             (user["id"], user_id, user["id"])).fetchall()
        count = lambda sql: conn.execute(sql, (user_id,)).fetchone()[0]  # noqa: E731
        return {
            "user": user_json(target) if own else {
                "id": target["id"], "name": target["name"], "handle": target["handle"], "bio": target["bio"],
                "coinsAccount": target["user_key"],
            },
            "following": _is_following(conn, user["id"], user_id),
            "followers": count("SELECT COUNT(*) FROM follows WHERE followee_id = ?"),
            "followingCount": count("SELECT COUNT(*) FROM follows WHERE follower_id = ?"),
            "postCount": count("SELECT COUNT(*) FROM posts WHERE user_id = ?"),
            "activity": [_activity_json(r) for r in activity],
            "posts": [_post_json(r) for r in posts],
        }

    return request.app.state.cache.respond(request, user["id"], [f"user:{user_id}"], compute)


@router.post("/follow/{user_id}")
def follow(user_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    if user_id == user["id"]:
        raise HTTPException(400, "self")
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(404, "not_found")
    _limit(request, user, "follow")
    added = conn.execute("INSERT INTO follows (follower_id, followee_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                         (user["id"], user_id, db.now_ms())).rowcount
    if added:
        notify.follow(conn, user_id, user["id"])
    _bump(request, "feed", "people", f"user:{user_id}", f"user:{user['id']}", f"notif:{user_id}")
    return {"ok": True}


@router.delete("/follow/{user_id}")
def unfollow(user_id: int, request: Request, user=Depends(current_user)):
    _limit(request, user, "follow")
    request.app.state.db.execute("DELETE FROM follows WHERE follower_id = ? AND followee_id = ?", (user["id"], user_id))
    _bump(request, "feed", "people", f"user:{user_id}", f"user:{user['id']}")
    return {"ok": True}


def _person(r) -> dict:
    return {"id": r["id"], "name": r["name"], "handle": r["handle"], "bio": r["bio"], "following": bool(r["following"])}


@router.get("/people")
def people(request: Request, q: str | None = None, user=Depends(current_user)):
    conn = request.app.state.db
    me = user["id"]
    if q is not None:
        term = q.strip()
        if len(term.lstrip("@")) < 2 or len(term) > 40:
            raise HTTPException(400, "bad_q")
        like = "%" + profanity.fold(term.lstrip("@")).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        handle_prefix = term.lstrip("@").lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = conn.execute(
            "SELECT u.id, u.name, u.handle, u.bio, (f.follower_id IS NOT NULL) AS following FROM users u"
            " LEFT JOIN follows f ON f.followee_id = u.id AND f.follower_id = ?"
            " WHERE u.id <> ? AND u.handle IS NOT NULL AND (u.handle LIKE ? OR u.name_folded LIKE ?)"
            " ORDER BY (u.handle LIKE ?) DESC, u.handle NULLS LAST LIMIT 30",
            (me, me, handle_prefix, like, handle_prefix),
        ).fetchall()
        return {"items": [_person(r) for r in rows]}

    def compute():
        rows = conn.execute(
            """
            SELECT id, name, handle, bio, following FROM (
              SELECT u.id, u.name, u.handle, u.bio, 1 AS following, f.created_at AS at
              FROM follows f JOIN users u ON u.id = f.followee_id WHERE f.follower_id = ?
              UNION ALL
              SELECT u.id, u.name, u.handle, u.bio, 0, MAX(p.created_at)
              FROM posts p JOIN users u ON u.id = p.user_id
              WHERE p.user_id <> ? AND p.user_id NOT IN (SELECT followee_id FROM follows WHERE follower_id = ?)
              GROUP BY u.id
            ) t ORDER BY following DESC, at DESC LIMIT 100
            """,
            (me, me, me),
        ).fetchall()
        return {"items": [_person(r) for r in rows]}

    return request.app.state.cache.respond(request, me, ["people"], compute)
