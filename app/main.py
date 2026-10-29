"""Kino Social backend: the one plugin server Kino's owner allowed (2026-10-28), served from his PC through a tunnel.

PostgreSQL holds the data, Redis the read cache and the budgets (memory when Redis is down or not configured).
"""
import logging
import os
import secrets

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import auth, db
from .cache import MemoryStore, ReadCache, RedisStore
from .ratelimit import RateLimiter, RedisRateLimiter

log = logging.getLogger("kino_social")
LOGIN_PER_HOUR = 30


def user_json(row) -> dict:
    return {"id": row["id"], "name": row["name"], "handle": row["handle"], "bio": row["bio"],
            "shareActivity": bool(row["share_activity"])}


def current_user(request: Request):
    header = request.headers.get("authorization", "")
    key = header[7:].strip() if header.lower().startswith("bearer ") else ""
    row = None
    if key:
        row = request.app.state.db.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.key = ? AND s.expires_at > ?",
            (key, db.now_ms()),
        ).fetchone()
    if row is None:
        raise HTTPException(401, "no_session")
    return row


def load_secret(path: str) -> bytes:
    """The HMAC secret users are keyed by: made once and kept (losing it loses every account)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) >= 32:
            return data
    except FileNotFoundError:
        pass
    data = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data


def create_app(database, *, public_key_b64: str = auth.PUBLIC_KEY_B64, secret: bytes,
               redis_client=None, allowed_authors: set[str] | None = None) -> FastAPI:
    """[database] a db.Database (or a DSN). [allowed_authors]: the plugin author fingerprints a session may come from
    (None: any, unsigned too — only for development)."""
    app = FastAPI(title="Kino Social")
    app.state.db = db.connect(database) if isinstance(database, str) else database
    app.state.public_key_b64 = public_key_b64
    app.state.secret = secret
    app.state.allowed_authors = allowed_authors
    if redis_client is not None:
        app.state.cache = ReadCache(RedisStore(redis_client), MemoryStore())
        app.state.limiter = RedisRateLimiter(redis_client)
        app.state.ip_limiter = RedisRateLimiter(redis_client, prefix="ks:rlip:")
    else:
        app.state.cache = ReadCache()
        app.state.limiter = RateLimiter()
        app.state.ip_limiter = RateLimiter()

    @app.exception_handler(HTTPException)
    async def _error(_request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/health")
    def health():
        return {"ok": True}

    def _login_gate(request: Request) -> None:
        ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
        if not app.state.ip_limiter.allow(ip, "login", LOGIN_PER_HOUR):
            raise HTTPException(429, "rate_limited")

    @app.post("/session/challenge")
    def challenge(request: Request):
        _login_gate(request)
        conn = app.state.db
        now = db.now_ms()
        conn.execute("DELETE FROM challenges WHERE expires_at <= ?", (now,))
        value = auth.new_challenge()
        conn.execute("INSERT INTO challenges (challenge, expires_at) VALUES (?, ?)", (value, now + auth.CHALLENGE_MS))
        return {"challenge": value, "expiresAt": now + auth.CHALLENGE_MS}

    @app.post("/session")
    def session(body: dict, request: Request):
        _login_gate(request)
        conn = app.state.db
        now = db.now_ms()
        challenge_value = body.get("challenge")
        # Spend the challenge first, whatever follows: one try per challenge.
        spent = isinstance(challenge_value, str) and conn.execute(
            "DELETE FROM challenges WHERE challenge = ? AND expires_at > ?", (challenge_value, now)).rowcount == 1
        who = auth.verify_gate_token(body.get("token"), app.state.public_key_b64)
        if who is None:
            raise HTTPException(401, "bad_token")
        author, code_hash = body.get("author", ""), body.get("codeHash", "")
        if not spent or not auth.verify_device_signature(body.get("deviceKey"), body.get("signature"), challenge_value,
                                                         author, code_hash):
            raise HTTPException(401, "bad_proof")
        allowed = app.state.allowed_authors
        if allowed is not None and author not in allowed:
            log.info("session refused: plugin not signed by an allowed author")
            raise HTTPException(403, "unofficial_plugin")
        tg, device_hash = who
        device_key = body["deviceKey"]
        iat = auth.token_iat(body["token"])
        ukey = auth.user_key(app.state.secret, tg)
        with conn.transaction() as tx:
            row = tx.execute("SELECT id FROM users WHERE user_key = ?", (ukey,)).fetchone()
            if row is None:
                name = auth.default_name(tg)
                row = tx.execute(
                    "INSERT INTO users (user_key, name, name_folded, created_at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT (user_key) DO UPDATE SET user_key = EXCLUDED.user_key RETURNING id",
                    (ukey, name, name.lower(), now)).fetchone()
            user_id = row["id"]
            bound = tx.execute("SELECT device_key, bound_iat FROM devices WHERE user_id = ? AND device_hash = ?",
                               (user_id, device_hash)).fetchone()
            if bound is None:
                if tx.execute("SELECT COUNT(*) FROM devices WHERE user_id = ?", (user_id,)).fetchone()[0] >= auth.MAX_DEVICES:
                    raise HTTPException(403, "too_many_devices")
                tx.execute("INSERT INTO devices (user_id, device_hash, device_key, created_at, bound_iat) VALUES (?, ?, ?, ?, ?)",
                           (user_id, device_hash, device_key, now, iat))
            elif bound["device_key"] != device_key:
                # A reinstall wipes the Keystore key but not the device hash: a token the bot signed after the binding
                # (the person validated again) moves it to the new key; the token the old key used cannot.
                if iat <= bound["bound_iat"]:
                    raise HTTPException(401, "device_key_mismatch")
                tx.execute("UPDATE devices SET device_key = ?, bound_iat = ? WHERE user_id = ? AND device_hash = ?",
                           (device_key, iat, user_id, device_hash))
                # Whatever the old key opened on this device ends: if it was a thief's, it loses the account with the rebind.
                tx.execute("DELETE FROM sessions WHERE user_id = ? AND device_hash = ?", (user_id, device_hash))
            elif iat > bound["bound_iat"]:
                # The bound key itself used a newer token: older tokens can no longer move the binding.
                tx.execute("UPDATE devices SET bound_iat = ? WHERE user_id = ? AND device_hash = ?", (iat, user_id, device_hash))
            key = auth.new_session_key()
            tx.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            tx.execute("INSERT INTO sessions (key, user_id, expires_at, device_hash) VALUES (?, ?, ?, ?)",
                       (key, user_id, now + auth.SESSION_MS, device_hash))
            user = tx.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return {"session": key, "user": user_json(user)}

    @app.post("/me/logout-all")
    def logout_all(user=Depends(current_user)):
        # Every session and every device binding: a device that lost its key (a reinstall) can log in again.
        with app.state.db.transaction() as tx:
            tx.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            tx.execute("DELETE FROM devices WHERE user_id = ?", (user["id"],))
        return {"ok": True}

    @app.get("/media/{media_id}")
    def media(media_id: str):
        row = app.state.db.execute("SELECT content_type, bytes FROM media WHERE id = ?", (media_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "not_found")
        return Response(bytes(row["bytes"]), media_type=row["content_type"],
                        headers={"Cache-Control": "public, max-age=604800, immutable", "X-Content-Type-Options": "nosniff"})

    @app.get("/me")
    def me(user=Depends(current_user)):
        return user_json(user)

    from . import communities, notify, social

    app.include_router(social.router)
    app.include_router(notify.router)
    app.include_router(communities.router)
    return app


def _env_file() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "data", "pg.env")
    out = {}
    try:
        with open(path, encoding="ascii") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    out[k] = v
    except FileNotFoundError:
        pass
    return out


def _serve() -> FastAPI:
    import redis

    env = {**_env_file(), **os.environ}
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    authors = {a.strip() for a in env.get("KINO_SOCIAL_AUTHORS", "").split(",") if a.strip()}
    redis_url = env.get("KINO_SOCIAL_REDIS")
    return create_app(
        env["KINO_SOCIAL_DSN"],
        public_key_b64=env.get("KINO_SOCIAL_PUBLIC_KEY", auth.PUBLIC_KEY_B64),
        secret=load_secret(os.path.join(data_dir, "secret.key")),
        redis_client=redis.Redis.from_url(redis_url, socket_timeout=0.5, socket_connect_timeout=0.5) if redis_url else None,
        allowed_authors=authors or None,
    )


app = _serve() if os.environ.get("KINO_SOCIAL_SERVE") == "1" else None
