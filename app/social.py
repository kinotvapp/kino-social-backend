"""The social API: posts, one-level comments, follows, watched activity and profiles."""
from fastapi import APIRouter, Depends, HTTPException, Request

from . import db, notify, profanity
from .main import current_user, user_json

router = APIRouter()

POST_MAX, COMMENT_MAX, NAME_MAX, BIO_MAX = 500, 300, 40, 160
LIMITS = {"post": 10, "comment": 30, "follow": 30}
FEED_ACTIVITY_WINDOW_MS = 3 * 86_400_000
TMDB_IMAGES = "https://image.tmdb.org/"


def _text(body: dict, key: str, max_len: int, *, allow_empty: bool = False) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise HTTPException(400, "bad_" + key)
    value = value.strip()
    if (not value and not allow_empty) or len(value) > max_len:
        raise HTTPException(400, "bad_" + key)
    return profanity.mask(value)


def _limit(request: Request, user, bucket: str) -> None:
    if not request.app.state.limiter.allow(user["id"], bucket, LIMITS[bucket]):
        raise HTTPException(429, "rate_limited")


def _author(row) -> dict:
    return {"id": row["user_id"], "name": row["author_name"]}


def _post_json(row) -> dict:
    item = {"type": "post", "id": row["id"], "user": _author(row), "text": row["text"],
            "comments": row["comment_count"], "createdAt": row["created_at"]}
    if row["link_url"]:
        link = {"url": row["link_url"]}
        if row["preview_title"]:
            link.update(title=row["preview_title"], desc=row["preview_desc"], site=row["preview_site"])
        if row["preview_media"]:
            link["image"] = row["preview_media"]
        item["link"] = link
    return item


def _activity_json(row, *, with_hidden: bool = False) -> dict:
    item = {"type": "activity", "id": row["id"], "user": _author(row), "kind": row["kind"], "title": row["title"],
            "tmdbId": row["tmdb_id"], "poster": row["poster"], "watchedAt": row["watched_at"],
            "createdAt": row["created_at"]}
    if row["season"] is not None:
        item["season"] = row["season"]
    if row["episode"] is not None:
        item["episode"] = row["episode"]
    if with_hidden:
        item["hidden"] = bool(row["hidden"])
    return item


POSTS_SQL = """
SELECT p.*, u.name AS author_name, (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count
FROM posts p JOIN users u ON u.id = p.user_id
"""
ACTIVITY_SQL = "SELECT a.*, u.name AS author_name FROM activity a JOIN users u ON u.id = a.user_id"


@router.patch("/me")
def update_me(body: dict, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    if "name" in body:
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (_text(body, "name", NAME_MAX), user["id"]))
    if "bio" in body:
        conn.execute("UPDATE users SET bio = ? WHERE id = ?", (_text(body, "bio", BIO_MAX, allow_empty=True), user["id"]))
    if "shareActivity" in body:
        if not isinstance(body["shareActivity"], bool):
            raise HTTPException(400, "bad_shareActivity")
        conn.execute("UPDATE users SET share_activity = ? WHERE id = ?", (int(body["shareActivity"]), user["id"]))
    return user_json(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())


@router.post("/posts")
def create_post(body: dict, request: Request, user=Depends(current_user)):
    text = _text(body, "text", POST_MAX)
    _limit(request, user, "post")
    link = link_for(request, text)
    conn = request.app.state.db
    cur = conn.execute(
        "INSERT INTO posts (user_id, text, link_url, preview_title, preview_desc, preview_site, preview_media, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], text, link.get("url"), link.get("title"), link.get("desc"), link.get("site"), link.get("image"),
         db.now_ms()),
    )
    return _post_json(conn.execute(POSTS_SQL + " WHERE p.id = ?", (cur.lastrowid,)).fetchone())


def link_for(request: Request, text: str) -> dict:
    """The post's link and its preview (filled in by preview.py); {} without a link."""
    from . import preview

    return preview.link_for(request.app, text)


def _owned(conn, table: str, row_id: int, user) -> None:
    row = conn.execute(f"SELECT user_id FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "not_found")
    if row["user_id"] != user["id"]:
        raise HTTPException(403, "not_yours")


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    _owned(conn, "posts", post_id, user)
    conn.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    return {"ok": True}


@router.get("/posts/{post_id}/comments")
def list_comments(post_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    if conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone() is None:
        raise HTTPException(404, "not_found")

    def compute():
        rows = conn.execute(
            "SELECT c.*, u.name AS author_name FROM comments c JOIN users u ON u.id = c.user_id"
            " WHERE c.post_id = ? ORDER BY c.created_at, c.id LIMIT 200",
            (post_id,),
        ).fetchall()
        return {"items": [{"id": r["id"], "user": _author(r), "text": r["text"], "createdAt": r["created_at"]}
                          for r in rows]}

    return request.app.state.cache.respond(request, user["id"], compute)


@router.post("/posts/{post_id}/comments")
def add_comment(post_id: int, body: dict, request: Request, user=Depends(current_user)):
    text = _text(body, "text", COMMENT_MAX)
    conn = request.app.state.db
    if conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone() is None:
        raise HTTPException(404, "not_found")
    _limit(request, user, "comment")
    now = db.now_ms()
    cur = conn.execute("INSERT INTO comments (post_id, user_id, text, created_at) VALUES (?, ?, ?, ?)",
                       (post_id, user["id"], text, now))
    notify.comment(conn, post_id, user["id"])
    return {"id": cur.lastrowid, "user": {"id": user["id"], "name": user["name"]}, "text": text, "createdAt": now}


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    _owned(conn, "comments", comment_id, user)
    conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    return {"ok": True}


@router.get("/feed")
def feed(request: Request, before: int | None = None, limit: int = 20, user=Depends(current_user)):
    return request.app.state.cache.respond(request, user["id"], lambda: _feed(request, user, before, limit))


def _feed(request: Request, user, before: int | None, limit: int) -> dict:
    limit = max(1, min(limit, 50))
    cutoff = before if before is not None else 2**62
    conn = request.app.state.db
    posts = conn.execute(POSTS_SQL + " WHERE p.created_at < ? ORDER BY p.created_at DESC, p.id DESC LIMIT ?",
                         (cutoff, limit)).fetchall()
    activity = conn.execute(
        ACTIVITY_SQL + " JOIN follows f ON f.followee_id = a.user_id AND f.follower_id = ?"
        " WHERE a.created_at < ? AND a.hidden = 0 AND u.share_activity = 1 AND a.watched_at >= a.created_at - ?"
        " ORDER BY a.created_at DESC, a.id DESC LIMIT ?",
        (user["id"], cutoff, FEED_ACTIVITY_WINDOW_MS, limit),
    ).fetchall()
    items = [_post_json(r) for r in posts] + [_activity_json(r) for r in activity]
    items.sort(key=lambda i: (i["createdAt"], i["type"] == "post", i["id"]), reverse=True)
    items = items[:limit]
    return {"items": items, "next": items[-1]["createdAt"] if len(items) == limit else None}


def _clean_activity(item) -> tuple | None:
    if not isinstance(item, dict) or item.get("adult") is True:
        return None
    kind, tmdb, title = item.get("kind"), item.get("tmdbId"), item.get("title")
    if kind not in ("movie", "series") or not isinstance(tmdb, int) or isinstance(tmdb, bool) or tmdb <= 0:
        return None
    if not isinstance(title, str) or not title.strip() or len(title) > 200 or profanity.looks_adult(title):
        return None
    season, episode = item.get("season"), item.get("episode")
    if kind == "movie" or not isinstance(season, int) or not isinstance(episode, int) or season < 0 or episode < 1:
        season = episode = None
    poster = item.get("poster")
    if not isinstance(poster, str) or not poster.startswith(TMDB_IMAGES) or len(poster) > 300:
        poster = None
    watched = item.get("watchedAtMs")
    if not isinstance(watched, int) or watched <= 0:
        return None
    return kind, tmdb, title.strip(), season, episode, poster, watched


@router.post("/activity")
def add_activity(body: dict, request: Request, user=Depends(current_user)):
    items = body.get("items")
    if not isinstance(items, list) or len(items) > 100:
        raise HTTPException(400, "bad_items")
    conn = request.app.state.db
    added = 0
    now = db.now_ms()
    for item in items:
        clean = _clean_activity(item)
        if clean is None:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO activity (user_id, kind, tmdb_id, title, season, episode, poster, watched_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], *clean, now),
        )
        added += cur.rowcount
    return {"added": added}


@router.get("/me/activity")
def my_activity(request: Request, user=Depends(current_user)):
    rows = request.app.state.db.execute(
        ACTIVITY_SQL + " WHERE a.user_id = ? ORDER BY a.watched_at DESC, a.id DESC LIMIT 100", (user["id"],)
    ).fetchall()
    return {"items": [_activity_json(r, with_hidden=True) for r in rows]}


@router.patch("/me/activity/{activity_id}")
def hide_activity(activity_id: int, body: dict, request: Request, user=Depends(current_user)):
    if not isinstance(body.get("hidden"), bool):
        raise HTTPException(400, "bad_hidden")
    conn = request.app.state.db
    _owned(conn, "activity", activity_id, user)
    conn.execute("UPDATE activity SET hidden = ? WHERE id = ?", (int(body["hidden"]), activity_id))
    return {"ok": True}


def _is_following(conn, follower: int, followee: int) -> bool:
    return conn.execute("SELECT 1 FROM follows WHERE follower_id = ? AND followee_id = ?",
                        (follower, followee)).fetchone() is not None


@router.get("/users/{user_id}")
def profile(user_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        raise HTTPException(404, "not_found")
    own = user_id == user["id"]
    activity = []
    if own or target["share_activity"]:
        activity = conn.execute(
            ACTIVITY_SQL + " WHERE a.user_id = ? AND a.hidden = 0 ORDER BY a.watched_at DESC, a.id DESC LIMIT 50",
            (user_id,),
        ).fetchall()
    posts = conn.execute(POSTS_SQL + " WHERE p.user_id = ? ORDER BY p.created_at DESC LIMIT 20", (user_id,)).fetchall()
    count = lambda sql: conn.execute(sql, (user_id,)).fetchone()[0]  # noqa: E731
    return {
        "user": user_json(target) if own else {"id": target["id"], "name": target["name"], "bio": target["bio"]},
        "following": _is_following(conn, user["id"], user_id),
        "followers": count("SELECT COUNT(*) FROM follows WHERE followee_id = ?"),
        "followingCount": count("SELECT COUNT(*) FROM follows WHERE follower_id = ?"),
        "activity": [_activity_json(r) for r in activity],
        "posts": [_post_json(r) for r in posts],
    }


@router.post("/follow/{user_id}")
def follow(user_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    if user_id == user["id"]:
        raise HTTPException(400, "self")
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(404, "not_found")
    _limit(request, user, "follow")
    conn.execute("INSERT OR IGNORE INTO follows (follower_id, followee_id, created_at) VALUES (?, ?, ?)",
                 (user["id"], user_id, db.now_ms()))
    notify.follow(conn, user_id, user["id"])
    return {"ok": True}


@router.delete("/follow/{user_id}")
def unfollow(user_id: int, request: Request, user=Depends(current_user)):
    _limit(request, user, "follow")
    request.app.state.db.execute("DELETE FROM follows WHERE follower_id = ? AND followee_id = ?", (user["id"], user_id))
    return {"ok": True}


@router.get("/people")
def people(request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    rows = conn.execute(
        """
        SELECT u.id, u.name, u.bio, 1 AS following, f.created_at AS at FROM follows f JOIN users u ON u.id = f.followee_id
        WHERE f.follower_id = :me
        UNION ALL
        SELECT u.id, u.name, u.bio, 0, MAX(p.created_at) FROM posts p JOIN users u ON u.id = p.user_id
        WHERE p.user_id != :me AND p.user_id NOT IN (SELECT followee_id FROM follows WHERE follower_id = :me)
        GROUP BY u.id
        ORDER BY following DESC, at DESC LIMIT 100
        """,
        {"me": user["id"]},
    ).fetchall()
    return {"items": [{"id": r["id"], "name": r["name"], "bio": r["bio"], "following": bool(r["following"])} for r in rows]}
