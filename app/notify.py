"""Notifications: comments on your posts and new followers. Kept few on purpose."""
from fastapi import APIRouter, Depends, HTTPException, Request

from . import db
from .main import current_user

router = APIRouter()


def comment(conn, post_id: int, actor_id: int) -> None:
    owner = conn.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if owner is None or owner["user_id"] == actor_id:
        return
    now = db.now_ms()
    group = conn.execute(
        "SELECT id FROM notifications WHERE user_id = ? AND kind = 'comment' AND post_id = ? AND read = 0",
        (owner["user_id"], post_id),
    ).fetchone()
    if group:
        conn.execute("UPDATE notifications SET count = count + 1, actor_id = ?, created_at = ? WHERE id = ?",
                     (actor_id, now, group["id"]))
    else:
        conn.execute("INSERT INTO notifications (user_id, kind, actor_id, post_id, created_at) VALUES (?, 'comment', ?, ?, ?)",
                     (owner["user_id"], actor_id, post_id, now))


def follow(conn, followee_id: int, actor_id: int) -> None:
    seen = conn.execute("SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'follow' AND actor_id = ?",
                        (followee_id, actor_id)).fetchone()
    if seen is None:
        conn.execute("INSERT INTO notifications (user_id, kind, actor_id, created_at) VALUES (?, 'follow', ?, ?)",
                     (followee_id, actor_id, db.now_ms()))


@router.get("/notifications")
def list_notifications(request: Request, since: int = 0, user=Depends(current_user)):
    conn = request.app.state.db
    rows = conn.execute(
        "SELECT n.*, u.name AS actor_name, p.text AS post_text FROM notifications n JOIN users u ON u.id = n.actor_id"
        " LEFT JOIN posts p ON p.id = n.post_id"
        " WHERE n.user_id = ? AND n.created_at > ? AND (n.post_id IS NULL OR p.id IS NOT NULL)"
        " ORDER BY n.created_at DESC, n.id DESC LIMIT 30",
        (user["id"], since),
    ).fetchall()
    unread = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0 AND created_at > ?",
                          (user["id"], since)).fetchone()[0]
    items = []
    for r in rows:
        item = {"id": r["id"], "kind": r["kind"], "actor": {"id": r["actor_id"], "name": r["actor_name"]},
                "count": r["count"], "createdAt": r["created_at"], "read": bool(r["read"])}
        if r["post_id"] is not None:
            item["postId"] = r["post_id"]
            item["postText"] = (r["post_text"] or "")[:80]
        items.append(item)
    return {"items": items, "unread": unread}


@router.post("/notifications/read")
def mark_read(body: dict, request: Request, user=Depends(current_user)):
    up_to = body.get("upTo")
    if not isinstance(up_to, int) or isinstance(up_to, bool):
        raise HTTPException(400, "bad_upTo")
    request.app.state.db.execute("UPDATE notifications SET read = 1 WHERE user_id = ? AND created_at <= ?",
                                 (user["id"], up_to))
    return {"ok": True}
