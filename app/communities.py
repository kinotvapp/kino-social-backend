"""Communities (owner, 2026-10-29): someone creates one, people join, and it has its own feed built from the same posts,
likes, comments and previews as the general one. Open: anyone reads a community and joins it; only members post; the owner
moderates (deletes posts in it) and cannot leave. A community's posts stay in its feed, never in the general one.

Notices stay few: one unread "new posts in <community>" per member and community (it points at the newest post), and one
unread "<n> joined <community>" for its owner."""
from fastapi import APIRouter, Depends, HTTPException, Request

from . import db, profanity
from .main import current_user
from .social import HANDLE, RESERVED, POSTS_SQL, _bump, _limit, _post_json, mix

router = APIRouter()

NAME_MIN, NAME_MAX, DESCRIPTION_MAX = 3, 40, 200
LIST_MAX = 30


def _json(row, viewer: int) -> dict:
    return {"id": row["id"], "slug": row["slug"], "name": row["name"], "description": row["description"],
            "members": row["member_count"], "posts": row["post_count"], "joined": bool(row["joined"]),
            "owner": row["owner_id"] == viewer, "createdAt": row["created_at"]}


SELECT = ("SELECT c.*, (m.user_id IS NOT NULL) AS joined FROM communities c"
          " LEFT JOIN community_members m ON m.community_id = c.id AND m.user_id = ?")


def _one(conn, community_id: int, viewer: int):
    row = conn.execute(SELECT + " WHERE c.id = ?", (viewer, community_id)).fetchone()
    if row is None:
        raise HTTPException(404, "not_found")
    return row


def postable(conn, community_id, user_id: int) -> int | None:
    """The community a new post goes to (None: the general feed); only its members may post there."""
    if community_id is None:
        return None
    if not isinstance(community_id, int) or isinstance(community_id, bool):
        raise HTTPException(400, "bad_community")
    if conn.execute("SELECT 1 FROM communities WHERE id = ?", (community_id,)).fetchone() is None:
        raise HTTPException(404, "not_found")
    if conn.execute("SELECT 1 FROM community_members WHERE community_id = ? AND user_id = ?",
                    (community_id, user_id)).fetchone() is None:
        raise HTTPException(403, "not_member")
    return community_id


def _grouped(conn, kind: str, community_id: int, users: list[int], actor_id: int, post_id: int | None) -> None:
    """One unread [kind] notice per user and community: it grows (count, newest actor and post) until it is read."""
    now = db.now_ms()
    for u in users:
        if u == actor_id:
            continue
        grown = conn.execute(
            "UPDATE notifications SET count = count + 1, actor_id = ?, post_id = ?, created_at = ?"
            " WHERE user_id = ? AND kind = ? AND community_id = ? AND NOT read",
            (actor_id, post_id, now, u, kind, community_id)).rowcount
        if not grown:
            conn.execute("INSERT INTO notifications (user_id, kind, actor_id, post_id, community_id, created_at)"
                         " VALUES (?, ?, ?, ?, ?, ?)", (u, kind, actor_id, post_id, community_id, now))


def posted(conn, community_id: int, author_id: int, post_id: int) -> list[int]:
    """A member posted: the others hear of it; the members told (their notice caches change)."""
    conn.execute("UPDATE communities SET post_count = post_count + 1 WHERE id = ?", (community_id,))
    members = [r["user_id"] for r in conn.execute("SELECT user_id FROM community_members WHERE community_id = ?", (community_id,))]
    _grouped(conn, "community_post", community_id, members, author_id, post_id)
    return [m for m in members if m != author_id]


@router.post("/communities")
def create(body: dict, request: Request, user=Depends(current_user)):
    name = body.get("name")
    if not isinstance(name, str) or not NAME_MIN <= len(name.strip()) <= NAME_MAX or profanity.looks_adult(name):
        raise HTTPException(400, "bad_name")
    slug = body.get("slug")
    slug = slug.strip().lstrip("@").lower() if isinstance(slug, str) else ""
    if not HANDLE.match(slug) or slug in RESERVED or profanity.looks_adult(slug):
        raise HTTPException(400, "bad_slug")
    description = body.get("description") or ""
    if not isinstance(description, str) or len(description) > DESCRIPTION_MAX or profanity.looks_adult(description):
        raise HTTPException(400, "bad_description")
    _limit(request, user, "community")
    conn = request.app.state.db
    name = profanity.mask(name.strip())
    now = db.now_ms()
    with conn.transaction() as tx:
        row = tx.execute(
            "INSERT INTO communities (slug, name, name_folded, description, owner_id, member_count, created_at)"
            " VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT (slug) DO NOTHING RETURNING id",
            (slug, name, profanity.fold(name), profanity.mask(description.strip()), user["id"], now)).fetchone()
        if row is None:
            raise HTTPException(409, "slug_taken")
        tx.execute("INSERT INTO community_members (community_id, user_id, created_at) VALUES (?, ?, ?)", (row["id"], user["id"], now))
    _bump(request, "communities")
    return _json(_one(conn, row["id"], user["id"]), user["id"])


@router.get("/communities")
def lists(request: Request, q: str | None = None, user=Depends(current_user)):
    conn = request.app.state.db
    me = user["id"]
    if q is not None:
        term = q.strip().lstrip("@")
        if len(term) < 2 or len(term) > 40:
            raise HTTPException(400, "bad_q")
        like = "%" + profanity.fold(term).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = conn.execute(SELECT + " WHERE c.slug LIKE ? OR c.name_folded LIKE ? ORDER BY c.member_count DESC, c.id LIMIT ?",
                            (me, like, like, LIST_MAX)).fetchall()
        return {"items": [_json(r, me) for r in rows]}

    def compute():
        mine = conn.execute(SELECT + " WHERE m.user_id IS NOT NULL ORDER BY c.name_folded LIMIT ?", (me, LIST_MAX)).fetchall()
        # Cold start: the biggest communities the person is not in yet.
        discover = conn.execute(SELECT + " WHERE m.user_id IS NULL ORDER BY c.member_count DESC, c.post_count DESC, c.id DESC LIMIT ?",
                                (me, LIST_MAX)).fetchall()
        return {"mine": [_json(r, me) for r in mine], "discover": [_json(r, me) for r in discover]}

    return request.app.state.cache.respond(request, me, ["communities"], compute)


@router.get("/communities/{community_id}")
def get(community_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    return request.app.state.cache.respond(request, user["id"], ["communities", f"community:{community_id}"],
                                           lambda: _json(_one(conn, community_id, user["id"]), user["id"]))


@router.get("/communities/{community_id}/feed")
def feed(community_id: int, request: Request, before: int | None = None, limit: int = 20, user=Depends(current_user)):
    conn = request.app.state.db

    def compute():
        _one(conn, community_id, user["id"])
        n = max(1, min(limit, 50))
        rows = conn.execute(POSTS_SQL + " WHERE p.community_id = ? AND p.created_at < ? ORDER BY p.created_at DESC, p.id DESC LIMIT ?",
                            (user["id"], community_id, before if before is not None else 2**62, n)).fetchall()
        page = [_post_json(r) for r in rows]
        return {"items": mix(page), "next": min(i["createdAt"] for i in page) if len(page) == n else None}

    return request.app.state.cache.respond(request, user["id"], ["feed", f"community:{community_id}"], compute)


@router.post("/communities/{community_id}/join")
def join(community_id: int, request: Request, user=Depends(current_user)):
    _limit(request, user, "follow")
    conn = request.app.state.db
    c = _one(conn, community_id, user["id"])
    with conn.transaction() as tx:
        added = tx.execute("INSERT INTO community_members (community_id, user_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                           (community_id, user["id"], db.now_ms())).rowcount
        if added:
            tx.execute("UPDATE communities SET member_count = member_count + 1 WHERE id = ?", (community_id,))
            _grouped(tx, "community_join", community_id, [c["owner_id"]], user["id"], None)
    _bump(request, "communities", f"community:{community_id}", f"notif:{c['owner_id']}")
    return _json(_one(conn, community_id, user["id"]), user["id"])


@router.delete("/communities/{community_id}/join")
def leave(community_id: int, request: Request, user=Depends(current_user)):
    conn = request.app.state.db
    c = _one(conn, community_id, user["id"])
    if c["owner_id"] == user["id"]:
        raise HTTPException(400, "owner_stays")
    with conn.transaction() as tx:
        if tx.execute("DELETE FROM community_members WHERE community_id = ? AND user_id = ?", (community_id, user["id"])).rowcount:
            tx.execute("UPDATE communities SET member_count = GREATEST(member_count - 1, 0) WHERE id = ?", (community_id,))
    _bump(request, "communities", f"community:{community_id}")
    return _json(_one(conn, community_id, user["id"]), user["id"])
