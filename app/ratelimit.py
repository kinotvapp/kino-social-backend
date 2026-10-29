"""Per-user (or per-address) budgets: a sliding window in memory, or a fixed hourly window in Redis shared by every
worker. Redis failing falls back to memory: a budget never blocks a request because the store is down."""
import logging
import time
from collections import defaultdict, deque

log = logging.getLogger("kino_social.ratelimit")


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[tuple, deque] = defaultdict(deque)

    def allow(self, who, bucket: str, limit: int, window_s: int = 3600) -> bool:
        now = self._clock()
        hits = self._hits[(who, bucket)]
        while hits and hits[0] <= now - window_s:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


class RedisRateLimiter:
    def __init__(self, client, prefix: str = "ks:rl:", clock=time.time):
        self.r = client
        self.p = prefix
        self._clock = clock
        self._memory = RateLimiter()

    def allow(self, who, bucket: str, limit: int, window_s: int = 3600) -> bool:
        slot = int(self._clock() // window_s)
        key = f"{self.p}{bucket}:{who}:{slot}"
        try:
            pipe = self.r.pipeline(transaction=False)
            pipe.incr(key)
            pipe.expire(key, window_s + 60)
            count = pipe.execute()[0]
        except Exception as e:
            log.warning("rate limit store failed (%s), using memory", type(e).__name__)
            return self._memory.allow(who, bucket, limit, window_s)
        return count <= limit
