"""PostgreSQL storage through a small connection pool. Every time is epoch milliseconds (BIGINT).

SQL in this app is written with `?` placeholders (converted to psycopg's `%s` here); each `execute` borrows a pooled
connection in autocommit, and `transaction()` groups the few writes that must land together.
"""
import time
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

SCHEMA = """
-- `id` is this server's own public number; `user_key` is HMAC(secret, telegram id): the Telegram id is never stored.
CREATE TABLE IF NOT EXISTS users (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  name_folded TEXT NOT NULL DEFAULT '',
  handle TEXT UNIQUE,
  bio TEXT NOT NULL DEFAULT '',
  share_activity BOOLEAN NOT NULL DEFAULT TRUE,
  created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  key TEXT PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
-- Which device's binding opened the session: a rebind ends only that device's sessions.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS device_hash TEXT;
-- Each install's P-256 key, bound to the gate token's device hash the first time that device logs in.
CREATE TABLE IF NOT EXISTS devices (
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_hash TEXT NOT NULL,
  device_key TEXT NOT NULL,
  created_at BIGINT NOT NULL,
  PRIMARY KEY (user_id, device_hash)
);
-- The issue time of the gate token that bound the key: only a newer one (the bot again, after a reinstall) may rebind.
ALTER TABLE devices ADD COLUMN IF NOT EXISTS bound_iat BIGINT NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS challenges (
  challenge TEXT PRIMARY KEY,
  expires_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  link_url TEXT,
  preview_title TEXT,
  preview_desc TEXT,
  preview_site TEXT,
  preview_media TEXT,
  comment_count INTEGER NOT NULL DEFAULT 0,
  like_count INTEGER NOT NULL DEFAULT 0,
  created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_created ON posts(created_at DESC, id DESC);
-- A TMDB or IMDb link's title as Kino knows it: the card opens it in Kino.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS preview_kind TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS preview_tmdb BIGINT;
CREATE INDEX IF NOT EXISTS posts_user ON posts(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS comments (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_post ON comments(post_id, created_at, id);
CREATE TABLE IF NOT EXISTS likes (
  post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at BIGINT NOT NULL,
  PRIMARY KEY (post_id, user_id)
);
CREATE INDEX IF NOT EXISTS likes_user ON likes(user_id);
CREATE TABLE IF NOT EXISTS follows (
  follower_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  followee_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at BIGINT NOT NULL,
  PRIMARY KEY (follower_id, followee_id)
);
CREATE INDEX IF NOT EXISTS follows_followee ON follows(followee_id);
CREATE TABLE IF NOT EXISTS activity (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  tmdb_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  season INTEGER,
  episode INTEGER,
  poster TEXT,
  watched_at BIGINT NOT NULL,
  hidden BOOLEAN NOT NULL DEFAULT FALSE,
  created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_user ON activity(user_id, created_at DESC);
-- An episode's own picture (TMDB still, 16:9) and name, when Kino knew them.
ALTER TABLE activity ADD COLUMN IF NOT EXISTS still TEXT;
ALTER TABLE activity ADD COLUMN IF NOT EXISTS episode_title TEXT;
-- COALESCE: a plain UNIQUE treats NULLs as distinct, so a movie (no season) would repeat.
CREATE UNIQUE INDEX IF NOT EXISTS activity_once
  ON activity(user_id, kind, tmdb_id, COALESCE(season, -1), COALESCE(episode, -1));
-- A comment or like group per post while unread (count grows), one follow per pair ever: few notifications by design.
CREATE TABLE IF NOT EXISTS notifications (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  actor_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  post_id BIGINT REFERENCES posts(id) ON DELETE CASCADE,
  count INTEGER NOT NULL DEFAULT 1,
  created_at BIGINT NOT NULL,
  read BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS notifications_user ON notifications(user_id, created_at DESC);
-- Who liked what before, so unliking and liking again never notifies twice.
CREATE TABLE IF NOT EXISTS like_notified (
  post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL,
  PRIMARY KEY (post_id, user_id)
);
CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY,
  content_type TEXT NOT NULL,
  bytes BYTEA NOT NULL
);
-- Communities: anyone creates one, anyone reads and joins, members post; a post belongs to at most one.
CREATE TABLE IF NOT EXISTS communities (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  name_folded TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  member_count INTEGER NOT NULL DEFAULT 0,
  post_count INTEGER NOT NULL DEFAULT 0,
  created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS community_members (
  community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at BIGINT NOT NULL,
  PRIMARY KEY (community_id, user_id)
);
CREATE INDEX IF NOT EXISTS community_members_user ON community_members(user_id);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS community_id BIGINT REFERENCES communities(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS posts_community ON posts(community_id, created_at DESC, id DESC);
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS community_id BIGINT REFERENCES communities(id) ON DELETE CASCADE;
-- Moderation (owner, 2026-10-29): a strike per masked post or comment, counted for 12 h; the third mutes for 12 h.
CREATE TABLE IF NOT EXISTS strikes (
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS strikes_user ON strikes(user_id, created_at);
ALTER TABLE users ADD COLUMN IF NOT EXISTS muted_until BIGINT NOT NULL DEFAULT 0;
-- Private communities (owner, 2026-10-29): joining is a request the owner approves or rejects.
ALTER TABLE communities ADD COLUMN IF NOT EXISTS private BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS community_requests (
  community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at BIGINT NOT NULL,
  PRIMARY KEY (community_id, user_id)
);
"""

TABLES = ("strikes", "like_notified", "notifications", "likes", "comments", "activity", "follows", "posts",
          "community_requests", "community_members", "communities", "devices", "sessions", "challenges", "media", "users")


def now_ms() -> int:
    return int(time.time() * 1000)  # time.time looked up at call time, so tests can move the clock


class Row(dict):
    """A row by column name, or by position (row[0]) for one-value queries."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


class Result:
    def __init__(self, rows: list, rowcount: int):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def _sql(text: str) -> str:
    return text.replace("%", "%%").replace("?", "%s")


class Database:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8):
        self.pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True,
                                   kwargs={"autocommit": True, "row_factory": _row_factory})
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)

    def execute(self, sql: str, params=()) -> Result:
        with self.pool.connection() as conn:
            return self._run(conn, sql, params)

    @staticmethod
    def _run(conn, sql, params) -> Result:
        cur = conn.execute(_sql(sql), tuple(params))
        rows = cur.fetchall() if cur.description else []
        return Result(rows, cur.rowcount)

    @contextmanager
    def transaction(self):
        """The writes inside land together (used by the few multi-row writes). Not shared between threads."""
        with self.pool.connection() as conn, conn.transaction():
            runner = _Bound(conn)
            yield runner

    def truncate_all(self) -> None:
        with self.pool.connection() as conn:
            conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")

    def close(self) -> None:
        self.pool.close()


class _Bound:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()) -> Result:
        return Database._run(self._conn, sql, params)


def connect(dsn: str) -> Database:
    return Database(dsn)
