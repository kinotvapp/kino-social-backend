"""Kino Social backend: the one plugin server Kino's owner allowed (2026-10-28), served from his PC through a tunnel."""
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import auth, db
from .ratelimit import RateLimiter


def user_json(row) -> dict:
    return {"id": row["id"], "name": row["name"], "bio": row["bio"], "shareActivity": bool(row["share_activity"])}


def current_user(request: Request):
    header = request.headers.get("authorization", "")
    key = header[7:].strip() if header.lower().startswith("bearer ") else ""
    conn = request.app.state.db
    row = None
    if key:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.key = ? AND s.expires_at > ?",
            (key, db.now_ms()),
        ).fetchone()
    if row is None:
        raise HTTPException(401, "no_session")
    return row


def create_app(db_path: str, *, public_key_b64: str = auth.PUBLIC_KEY_B64, fetcher=None) -> FastAPI:
    app = FastAPI(title="Kino Social")
    app.state.db = db.connect(db_path)
    app.state.public_key_b64 = public_key_b64
    app.state.fetcher = fetcher
    app.state.limiter = RateLimiter()

    @app.exception_handler(HTTPException)
    async def _error(_request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/session")
    def session(body: dict):
        who = auth.verify_gate_token(body.get("token"), app.state.public_key_b64)
        if who is None:
            raise HTTPException(401, "bad_token")
        tg, username = who
        conn = app.state.db
        now = db.now_ms()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (tg,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (id, username, name, created_at) VALUES (?, ?, ?, ?)",
                (tg, username, auth.default_name(tg, username), now),
            )
        elif username and row["username"] != username:
            # The name follows a new @username only while the person never typed their own.
            if row["name"] == auth.default_name(tg, row["username"]):
                conn.execute("UPDATE users SET name = ? WHERE id = ?", (auth.default_name(tg, username), tg))
            conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, tg))
        key = auth.new_session_key()
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        conn.execute("INSERT INTO sessions (key, user_id, expires_at) VALUES (?, ?, ?)", (key, tg, now + auth.SESSION_MS))
        user = conn.execute("SELECT * FROM users WHERE id = ?", (tg,)).fetchone()
        return {"session": key, "user": user_json(user)}

    @app.get("/media/{media_id}")
    def media(media_id: str):
        row = app.state.db.execute("SELECT content_type, bytes FROM media WHERE id = ?", (media_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "not_found")
        return Response(row["bytes"], media_type=row["content_type"],
                        headers={"Cache-Control": "public, max-age=604800", "X-Content-Type-Options": "nosniff"})

    @app.get("/me")
    def me(user=Depends(current_user)):
        return user_json(user)

    from . import social

    app.include_router(social.router)
    return app


def _serve() -> FastAPI:
    from .preview import Fetcher

    return create_app(
        os.environ.get("KINO_SOCIAL_DB", "data/social.db"),
        public_key_b64=os.environ.get("KINO_SOCIAL_PUBLIC_KEY", auth.PUBLIC_KEY_B64),
        fetcher=Fetcher(public_url=os.environ.get("KINO_SOCIAL_PUBLIC_URL", "https://social.comparadorinternet.co")),
    )


app = _serve() if os.environ.get("KINO_SOCIAL_SERVE") == "1" else None
