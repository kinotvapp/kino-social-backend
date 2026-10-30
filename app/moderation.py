"""Strikes and mutes (owner, 2026-10-29).

A new post or comment that got masked is a strike: the server's own list hit, or the text already carries the mask word
"xxxx" (the device's list or Kino's AI put it there). One strike per post or comment. Only the last 12 h count; the third
in that window mutes the person for 12 h (and clears the strikes): no new posts or comments, everything else allowed.
"""
from fastapi import HTTPException

from . import db, profanity

LIMIT = 3
WINDOW_MS = 12 * 3600 * 1000
MUTE_MS = 12 * 3600 * 1000


class Muted(HTTPException):
    """403 {"error": "muted", "mutedUntil": <ms>}: main.py's handler adds `extra` to the error body."""

    def __init__(self, until: int):
        super().__init__(403, "muted")
        self.extra = {"mutedUntil": until}


def offends(raw: str) -> bool:
    """Whether the text as sent earns a strike: a word of the server's list, or a "xxxx" the device already masked."""
    return profanity.has_swear(raw) or profanity.has_mask(raw)


def muted_until(user, now: int | None = None) -> int:
    """The end of the person's mute, or 0 when they are not muted."""
    until = int(user.get("muted_until") or 0)
    return until if until > (now if now is not None else db.now_ms()) else 0


def check(user) -> None:
    """Refuses a new post or comment while the person is muted."""
    until = muted_until(user)
    if until:
        raise Muted(until)


def strike(conn, user_id: int) -> dict:
    """Counts one strike for a post or comment already saved; the third within WINDOW_MS starts the mute.
    Returns the response's `moderation` object."""
    now = db.now_ms()
    with conn.transaction() as tx:
        tx.execute("DELETE FROM strikes WHERE user_id = ? AND created_at <= ?", (user_id, now - WINDOW_MS))
        tx.execute("INSERT INTO strikes (user_id, created_at) VALUES (?, ?)", (user_id, now))
        count = tx.execute("SELECT COUNT(*) FROM strikes WHERE user_id = ?", (user_id,)).fetchone()[0]
        out = {"strikes": count, "limit": LIMIT}
        if count >= LIMIT:
            until = now + MUTE_MS
            tx.execute("UPDATE users SET muted_until = ? WHERE id = ?", (until, user_id))
            tx.execute("DELETE FROM strikes WHERE user_id = ?", (user_id,))
            out["mutedUntil"] = until
    return out
