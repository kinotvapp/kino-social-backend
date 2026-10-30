"""Kino coins HTTP API. Sessions work like Kino Social's; the rest is /balance, /watch and /give (see ledger.py for the rules)."""
import os
import time

from fastapi import Depends, FastAPI, HTTPException, Request

from coins import auth, db as coins_db, ledger

LOGIN_PER_HOUR = 30
GIVE_PER_MINUTE = 60
MAX_BLOCK_SECONDS = 600


class _Limiter:
    """A small in-process counter per (key, bucket): enough for one process; a shared limiter is a later concern."""

    def __init__(self):
        self.hits: dict[tuple, list[float]] = {}

    def allow(self, key: str, bucket: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        hits = [t for t in self.hits.get((key, bucket), []) if now - t < window]
        if len(hits) >= limit:
            self.hits[(key, bucket)] = hits
            return False
        hits.append(now)
        self.hits[(key, bucket)] = hits
        return True


def current_account(request: Request) -> str:
    header = request.headers.get("authorization", "")
    key = header[7:].strip() if header.lower().startswith("bearer ") else ""
    row = None
    if key:
        row = request.app.state.db.execute(
            "SELECT account FROM coin_sessions WHERE key = ? AND expires_at > ?", (key, coins_db.now_ms())).fetchone()
    if row is None:
        raise HTTPException(401, "no_session")
    return row["account"]


def load_secret(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) >= 32:
            return data
    except FileNotFoundError:
        pass
    raise RuntimeError("the account secret file is missing: it must equal Kino Social's secret so account ids match")


def create_app(database, *, public_key_b64: str = auth.PUBLIC_KEY_B64, secret: bytes) -> FastAPI:
    app = FastAPI(title="Kino coins")
    app.state.db = coins_db.connect(database) if isinstance(database, str) else database
    app.state.public_key_b64 = public_key_b64
    app.state.secret = secret
    limiter = _Limiter()

    @app.get("/health")
    def health():
        return {"ok": True}

    def _login_gate(request: Request) -> None:
        ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
        if not limiter.allow(ip, "login", LOGIN_PER_HOUR, 3600):
            raise HTTPException(429, "rate_limited")

    @app.post("/session/challenge")
    def challenge(request: Request):
        _login_gate(request)
        now = coins_db.now_ms()
        app.state.db.execute("DELETE FROM coin_challenges WHERE expires_at <= ?", (now,))
        value = auth.new_challenge()
        app.state.db.execute("INSERT INTO coin_challenges (challenge, expires_at) VALUES (?, ?)", (value, now + auth.CHALLENGE_MS))
        return {"challenge": value, "expiresAt": now + auth.CHALLENGE_MS}

    @app.post("/session")
    def session(body: dict, request: Request):
        _login_gate(request)
        conn = app.state.db
        now = coins_db.now_ms()
        challenge_value = body.get("challenge")
        spent = isinstance(challenge_value, str) and conn.execute(
            "DELETE FROM coin_challenges WHERE challenge = ? AND expires_at > ?", (challenge_value, now)).rowcount == 1
        who = auth.verify_gate_token(body.get("token"), app.state.public_key_b64)
        if who is None:
            raise HTTPException(401, "bad_token")
        if not spent or not auth.verify_device_signature(body.get("deviceKey"), body.get("signature"), challenge_value,
                                                         body.get("author", ""), body.get("codeHash", "")):
            raise HTTPException(401, "bad_proof")
        tg, device_hash = who
        device_key = body["deviceKey"]
        iat = auth.token_iat(body["token"])
        account = auth.account_id(app.state.secret, tg)
        with conn.transaction() as tx:
            bound = tx.execute("SELECT device_key, bound_iat FROM coin_devices WHERE account = ? AND device_hash = ?",
                               (account, device_hash)).fetchone()
            if bound is None:
                if tx.execute("SELECT COUNT(*) FROM coin_devices WHERE account = ?", (account,)).fetchone()[0] >= auth.MAX_DEVICES:
                    raise HTTPException(403, "too_many_devices")
                tx.execute("INSERT INTO coin_devices (account, device_hash, device_key, created_at, bound_iat) VALUES (?, ?, ?, ?, ?)",
                           (account, device_hash, device_key, now, iat))
            elif bound["device_key"] != device_key:
                # A reinstall wipes the Keystore key but not the device hash: only a token the bot signed after the binding moves it.
                if iat <= bound["bound_iat"]:
                    raise HTTPException(401, "device_key_mismatch")
                tx.execute("UPDATE coin_devices SET device_key = ?, bound_iat = ? WHERE account = ? AND device_hash = ?",
                           (device_key, iat, account, device_hash))
                tx.execute("DELETE FROM coin_sessions WHERE account = ? AND device_hash = ?", (account, device_hash))
            elif iat > bound["bound_iat"]:
                tx.execute("UPDATE coin_devices SET bound_iat = ? WHERE account = ? AND device_hash = ?", (iat, account, device_hash))
            key = auth.new_session_key()
            tx.execute("DELETE FROM coin_sessions WHERE expires_at <= ?", (now,))
            tx.execute("INSERT INTO coin_sessions (key, account, device_hash, expires_at) VALUES (?, ?, ?, ?)",
                       (key, account, device_hash, now + auth.SESSION_MS))
        return {"session": key, "account": account}

    @app.get("/balance")
    def balance(account=Depends(current_account)):
        now = coins_db.now_ms()
        return {"balance": ledger.balance(app.state.db, account), "earnedToday": ledger.earned_today(app.state.db, account, now),
                "capToday": ledger.DAILY_CAP, "account": account}

    @app.post("/watch")
    def watch(body: dict, account=Depends(current_account)):
        block_id, seconds = body.get("blockId"), body.get("seconds")
        if (not isinstance(block_id, str) or not 8 <= len(block_id) <= 64 or not isinstance(seconds, int)
                or isinstance(seconds, bool) or not 1 <= seconds <= MAX_BLOCK_SECONDS):
            raise HTTPException(400, "bad_block")
        credited, earned = ledger.credit_watch(app.state.db, account, block_id, seconds, coins_db.now_ms())
        return {"credited": credited, "earnedToday": earned, "balance": ledger.balance(app.state.db, account)}

    @app.post("/give")
    def give(body: dict, account=Depends(current_account)):
        if not limiter.allow(account, "give", GIVE_PER_MINUTE, 60):
            raise HTTPException(429, "rate_limited")
        context = body.get("postId")
        if not isinstance(context, str) or not 1 <= len(context) <= 64:
            raise HTTPException(400, "bad_post")
        try:
            left = ledger.give(app.state.db, account, body.get("to"), context, coins_db.now_ms())
        except ledger.CoinsError as e:
            raise HTTPException(400, e.code)
        return {"balance": left, "postTotal": ledger.post_total(app.state.db, context)}

    @app.get("/posts/{post_id}/total")
    def total(post_id: str, account=Depends(current_account)):
        return {"total": ledger.post_total(app.state.db, post_id)}

    return app


def _serve() -> FastAPI | None:
    if os.environ.get("KINO_COINS_SERVE") != "1":
        return None
    dsn = os.environ["KINO_COINS_DSN"]
    secret = load_secret(os.environ.get("KINO_COINS_ACCOUNT_SECRET", "data/secret.key"))
    return create_app(dsn, public_key_b64=os.environ.get("KINO_COINS_PUBLIC_KEY", auth.PUBLIC_KEY_B64), secret=secret)


app = _serve()
