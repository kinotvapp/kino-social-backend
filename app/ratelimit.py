"""In-memory sliding windows per user and bucket; reset when the server restarts (fine for the experiment)."""
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[tuple[int, str], deque] = defaultdict(deque)

    def allow(self, user_id: int, bucket: str, limit: int, window_s: int = 3600) -> bool:
        now = self._clock()
        hits = self._hits[(user_id, bucket)]
        while hits and hits[0] <= now - window_s:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True
