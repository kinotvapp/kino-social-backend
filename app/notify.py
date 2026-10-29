"""Notifications: comments and likes on your posts, new followers, new posts of people you follow. Kept few on purpose:
one unread group per post and kind, one follow notice per pair ever, one like notice per person and post ever, one unread
new-posts notice per author (it points at the newest)."""
from fastapi import APIRouter, Depends, HTTPException, Request

from . import db
from .main import current_user

router = APIRouter()


def _group(conn, kind: str, post_id: int, owner: int, actor_id: int) -> None:
    if owner == actor_id:
        return
    now = db.now_ms()
    updated = conn.execute(
        "UPDATE notifications SET count = count + 1, actor_id = ?, created_at = ?"
        " WHERE user_id = ? AND kind = ? AND post_id = ? AND NOT read",
        (actor_id, now, owner, kind, post_id),
    ).rowcount
    if not updated:
        conn.execute("INSERT INTO notifications (user_id, kind, actor_id, post_id, created_at) VALUES (?, ?, ?, ?, ?)",
                     (owner, kind, actor_id, post_id, now))


def comment(conn, post_id: int, owner: int, actor_id: int) -> None:
    _group(conn, "comment", post_id, owner, actor_id)


def like(conn, post_id: int, owner: int, actor_id: int) -> None:
    if owner == actor_id:
        return
    first = conn.execute("INSERT INTO like_notified (post_id, user_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                         (post_id, actor_id)).rowcount
    if first:
        _group(conn, "like", post_id, owner, actor_id)


def follow(conn, followee_id: int, actor_id: int) -> None:
    seen = conn.execute("SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'follow' AND actor_id = ?",
                        (followee_id, actor_id)).fetchone()
    if seen is None:
        conn.execute("INSERT INTO notifications (user_id, kind, actor_id, created_at) VALUES (?, 'follow', ?, ?)",
                     (followee_id, actor_id, db.now_ms()))


def new_post(conn, author_id: int, post_id: int) -> list[int]:
    """Each follower of [author_id] hears of [post_id]; the followers told (their notification caches change)."""
    now = db.now_ms()
    grown = {r["user_id"] for r in conn.execute(
        "UPDATE notifications SET count = count + 1, post_id = ?, created_at = ? WHERE kind = 'post' AND actor_id = ? AND NOT read"
        " AND user_id IN (SELECT follower_id FROM follows WHERE followee_id = ?) RETURNING user_id",
        (post_id, now, author_id, author_id))}
    followers = [r["follower_id"] for r in conn.execute("SELECT follower_id FROM follows WHERE followee_id = ?", (author_id,))]
    for f in followers:
        if f not in grown:
            conn.execute("INSERT INTO notifications (user_id, kind, actor_id, post_id, created_at) VALUES (?, 'post', ?, ?, ?)",
                         (f, author_id, post_id, now))
    return followers


@router.get("/notifications")
def list_notifications(request: Request, since: int = 0, user=Depends(current_user)):
    conn = request.app.state.db

    def compute():
        rows = conn.execute(
            "SELECT n.*, u.name AS actor_name, u.handle AS actor_handle, p.text AS post_text, c.name AS community_name,"
            " c.slug AS community_slug FROM notifications n"
            " JOIN users u ON u.id = n.actor_id LEFT JOIN posts p ON p.id = n.post_id LEFT JOIN communities c ON c.id = n.community_id"
            " WHERE n.user_id = ? AND n.created_at > ? ORDER BY n.created_at DESC, n.id DESC LIMIT 30",
            (user["id"], since),
        ).fetchall()
        unread = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND NOT read AND created_at > ?",
                              (user["id"], since)).fetchone()[0]
        items = []
        for r in rows:
            item = {"id": r["id"], "kind": r["kind"], "actor": {"id": r["actor_id"], "name": r["actor_name"], "handle": r["actor_handle"]},
                    "count": r["count"], "createdAt": r["created_at"], "read": bool(r["read"])}
            if r["post_id"] is not None:
                item["postId"] = r["post_id"]
                item["postText"] = (r["post_text"] or "")[:80]
            if r["community_id"] is not None:
                item["community"] = {"id": r["community_id"], "name": r["community_name"], "slug": r["community_slug"]}
            items.append(item)
        return {"items": items, "unread": unread}

    return request.app.state.cache.respond(request, user["id"], [f"notif:{user['id']}"], compute)


@router.post("/notifications/read")
def mark_read(body: dict, request: Request, user=Depends(current_user)):
    up_to = body.get("upTo")
    if not isinstance(up_to, int) or isinstance(up_to, bool):
        raise HTTPException(400, "bad_upTo")
    request.app.state.db.execute("UPDATE notifications SET read = TRUE WHERE user_id = ? AND created_at <= ?", (user["id"], up_to))
    request.app.state.cache.bump(f"notif:{user['id']}")
    return {"ok": True}
