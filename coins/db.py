"""PostgreSQL storage for the coins service: its own tables (`coin_*`), its own DSN (KINO_COINS_DSN), its own pool.

Same conventions as Kino Social's db.py (`?` placeholders, epoch milliseconds) but a separate copy on purpose, so the service
can be lifted out of Social's repo without dragging Social's schema along."""
import time
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS coin_ledger (
    id BIGSERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    amount INTEGER NOT NULL,
    kind TEXT NOT NULL,
    counterpart TEXT,
    context_id TEXT,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS coin_ledger_account ON coin_ledger (account);
CREATE INDEX IF NOT EXISTS coin_ledger_context ON coin_ledger (context_id) WHERE kind = 'gift_in';
CREATE TABLE IF NOT EXISTS coin_blocks (block_id TEXT PRIMARY KEY, account TEXT NOT NULL, created_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS coin_daily (
    account TEXT NOT NULL,
    day BIGINT NOT NULL,
    carry_seconds INTEGER NOT NULL DEFAULT 0,
    coins INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account, day)
);
CREATE TABLE IF NOT EXISTS coin_challenges (challenge TEXT PRIMARY KEY, expires_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS coin_devices (
    account TEXT NOT NULL,
    device_hash TEXT NOT NULL,
    device_key TEXT NOT NULL,
    bound_iat BIGINT NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL,
    PRIMARY KEY (account, device_hash)
);
CREATE TABLE IF NOT EXISTS coin_sessions (
    key TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    device_hash TEXT NOT NULL,
    expires_at BIGINT NOT NULL
);
"""

TABLES = ["coin_ledger", "coin_blocks", "coin_daily", "coin_challenges", "coin_devices", "coin_sessions"]


def now_ms() -> int:
    return int(time.time() * 1000)


class Row(dict):
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


def _sql(text: str) -> str:
    return text.replace("%", "%%").replace("?", "%s")


def _run(conn, sql, params) -> Result:
    cur = conn.execute(_sql(sql), tuple(params))
    rows = cur.fetchall() if cur.description else []
    return Result(rows, cur.rowcount)


class _Bound:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()) -> Result:
        return _run(self._conn, sql, params)


class Database:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8):
        self.pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True,
                                   kwargs={"autocommit": True, "row_factory": _row_factory})
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)

    def execute(self, sql: str, params=()) -> Result:
        with self.pool.connection() as conn:
            return _run(conn, sql, params)

    @contextmanager
    def transaction(self):
        with self.pool.connection() as conn, conn.transaction():
            yield _Bound(conn)

    def truncate_all(self) -> None:
        with self.pool.connection() as conn:
            conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")

    def close(self) -> None:
        self.pool.close()


def connect(dsn: str) -> Database:
    return Database(dsn)
