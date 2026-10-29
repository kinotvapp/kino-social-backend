"""A read cache for the feed and the comments.

One global version counter moves on every successful write (any non-GET request, see the middleware in main.py), so a
cached answer is never stale. Answers are kept per (path, query, viewer, version) in a small LRU, and each carries an
ETag: a plugin that refreshes every minute and sends If-None-Match gets a body-less 304 while nothing changed.
"""
import hashlib
import json
from collections import OrderedDict

from fastapi import Request
from fastapi.responses import Response

MAX_ENTRIES = 500


class ReadCache:
    def __init__(self):
        self.version = 0
        self._entries: OrderedDict[tuple, tuple[str, bytes]] = OrderedDict()

    def bump(self) -> None:
        self.version += 1
        self._entries.clear()

    def respond(self, request: Request, viewer_id: int, compute) -> Response:
        key = (request.url.path, request.url.query, viewer_id)
        digest = hashlib.sha1(repr(key).encode()).hexdigest()[:12]
        etag = f'W/"{self.version}-{digest}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        cached = self._entries.get((key, self.version))
        if cached is None:
            cached = (etag, json.dumps(compute(), ensure_ascii=False, separators=(",", ":")).encode())
            self._entries[(key, self.version)] = cached
            while len(self._entries) > MAX_ENTRIES:
                self._entries.popitem(last=False)
        else:
            self._entries.move_to_end((key, self.version))
        return Response(cached[1], media_type="application/json", headers=headers)
