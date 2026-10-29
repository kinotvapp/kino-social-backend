"""A scoped read cache with ETags, in memory or in Redis.

Each answer names the scopes it reads ("feed", "post:12", "user:3", "people", "notif:3"); each write bumps the scopes it
changes. An answer is stored under its path, query, viewer and the current version of every scope it names, so a stale
answer is never served and a write never throws away answers it did not touch. The ETag carries the same versions:
a plugin refreshing with If-None-Match gets a body-less 304 while nothing it reads changed.
"""
import hashlib
import json
import logging
from collections import OrderedDict

from fastapi import Request
from fastapi.responses import Response

log = logging.getLogger("kino_social.cache")
MAX_ENTRIES = 1000
TTL_S = 300


class MemoryStore:
    def __init__(self):
        self.versions: dict[str, int] = {}
        self.entries: OrderedDict[str, bytes] = OrderedDict()

    def versions_of(self, scopes):
        return [self.versions.get(s, 0) for s in scopes]

    def bump(self, scopes):
        for s in scopes:
            self.versions[s] = self.versions.get(s, 0) + 1

    def get(self, key):
        body = self.entries.get(key)
        if body is not None:
            self.entries.move_to_end(key)
        return body

    def put(self, key, body):
        self.entries[key] = body
        while len(self.entries) > MAX_ENTRIES:
            self.entries.popitem(last=False)


class RedisStore:
    """Versions are Redis counters (they survive a restart, so clients' ETags stay valid); answers expire after TTL_S."""

    def __init__(self, client, prefix: str = "ks:"):
        self.r = client
        self.p = prefix

    def versions_of(self, scopes):
        values = self.r.mget([self.p + "v:" + s for s in scopes]) if scopes else []
        return [int(v) if v is not None else 0 for v in values]

    def bump(self, scopes):
        pipe = self.r.pipeline(transaction=False)
        for s in scopes:
            pipe.incr(self.p + "v:" + s)
        pipe.execute()

    def get(self, key):
        return self.r.get(self.p + "e:" + key)

    def put(self, key, body):
        self.r.set(self.p + "e:" + key, body, ex=TTL_S)


class ReadCache:
    def __init__(self, store=None, fallback: MemoryStore | None = None):
        self.store = store or MemoryStore()
        self.fallback = fallback or (MemoryStore() if store is not None else self.store)

    def _call(self, name, *args):
        try:
            return getattr(self.store, name)(*args)
        except Exception as e:  # Redis down: keep answering from memory, never fail a request over the cache
            log.warning("cache %s failed (%s), using memory", name, type(e).__name__)
            return getattr(self.fallback, name)(*args)

    def bump(self, *scopes: str) -> None:
        self._call("bump", list(scopes))
        if self.fallback is not self.store:
            self.fallback.bump(list(scopes))

    def respond(self, request: Request, viewer_id: int, scopes: list[str], compute) -> Response:
        versions = self._call("versions_of", scopes)
        ident = repr((request.url.path, request.url.query, viewer_id, scopes, versions))
        digest = hashlib.sha1(ident.encode()).hexdigest()[:20]
        etag = f'W/"{digest}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        body = self._call("get", digest)
        if body is None:
            body = json.dumps(compute(), ensure_ascii=False, separators=(",", ":")).encode()
            self._call("put", digest, body)
        return Response(body, media_type="application/json", headers=headers)
