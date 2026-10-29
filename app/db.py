"""SQLite storage. Every time is epoch milliseconds."""
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT,
  name TEXT,
  share_activity INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
  key TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  link_url TEXT,
  preview_title TEXT,
  preview_desc TEXT,
  preview_site TEXT,
  preview_media TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_created ON posts(created_at);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY,
  post_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_post ON comments(post_id);
CREATE TABLE IF NOT EXISTS follows (
  follower_id INTEGER NOT NULL,
  followee_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (follower_id, followee_id)
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  tmdb_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  season INTEGER,
  episode INTEGER,
  poster TEXT,
  watched_at INTEGER NOT NULL,
  hidden INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  UNIQUE (user_id, kind, tmdb_id, season, episode)
);
CREATE INDEX IF NOT EXISTS activity_created ON activity(created_at);
CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY,
  content_type TEXT NOT NULL,
  bytes BLOB NOT NULL
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
