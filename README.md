# Kino Social backend

> **Since 2026-10-29 the live server runs on Oracle Cloud** (https://129-158-250-81.sslip.io): see [ORACLE.md](ORACLE.md) and `scripts/oracle.sh`. The setup below is this PC's old copy; do not start it again.

The server of the Kino Social plugin (an experiment; the one plugin Kino's owner allowed a backend for, 2026-10-28).
FastAPI on this PC, `127.0.0.1:8787`, reached only through the Cloudflare tunnel `kino-social`
(`d3dfd239-fa2d-488f-8878-12b8f834642a`) at **https://social.comparadorinternet.co**.
PostgreSQL 18 and Redis 8 run inside WSL Ubuntu (`127.0.0.1:5432`, `127.0.0.1:6379`, password-protected).

## Run

```powershell
.\start-all.ps1          # WSL services + API + tunnel in the background, logs in .\logs
.\run.ps1                # API only, in the foreground
.\.venv\Scripts\python.exe -m pytest -q    # uses the kino_social_test database and Redis db 15
```

First time on a new machine: `wsl -d Ubuntu -u root -e sh scripts/pg-setup.sh` (databases) and
`scripts/redis-setup.sh` (password); `data\pg.env` holds `KINO_SOCIAL_DSN`, `KINO_SOCIAL_TEST_DSN`, `KINO_SOCIAL_REDIS` and,
in production, `KINO_SOCIAL_AUTHORS` (the plugin author fingerprints allowed to log in). `data\secret.key` is the HMAC
secret users are keyed by: **losing it loses every account**. Neither file is in git.

Start at logon (not registered automatically; run it yourself if you want it):

```powershell
$a = New-ScheduledTaskAction -Execute powershell.exe -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\dev\kino-social-backend\start-all.ps1"
Register-ScheduledTask -TaskName KinoSocial -Action $a -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Force
```

## Who may call it

- Every endpoint but `/health`, `/media/<id>` and `/session*` needs `Authorization: Bearer <session>`.
- A session needs: Kino's gate token (Ed25519, signed by @kinocodigo_bot for a validated device), a one-use challenge
  (2 min) signed by the install's own Keystore key for this plugin, and — with `KINO_SOCIAL_AUTHORS` set — Kino's
  attestation that the plugin asking is signed by an allowed author (`kino-identity-v2`: plugin, challenge, author
  fingerprint, entry SHA-256). A device's key is bound to the token's device hash the first time; another key is refused
  (`device_key_mismatch`). At most 6 devices per person; 30 login tries an hour per address.
- The Telegram id is never stored: users are `HMAC(secret, telegram id)`; the public ids are this server's own numbers.
  Display names default to «Usuario XXXX»; `@handle` is chosen by the person (unique, case-insensitive).
- Limits per person per hour: 10 posts, 30 comments, 60 likes, 30 follows, 10 handle changes.
- Moderation (`app/moderation.py`): a new post or comment that gets masked (the server's list hit, or the text already
  holds the device's `xxxx`) is a strike; strikes count for 12 h; the third mutes the person for 12 h (strikes cleared).
  The response then carries `moderation: {strikes, limit: 3, mutedUntil?}`; while muted, posts and comments answer 403
  `{"error": "muted", "mutedUntil": <ms>}` and `GET /me` says `mutedUntil`. Likes, follows and reading stay allowed.
  `SWEARS` is kept identical to the plugin's `src/swear.js` (`tests/test_swear_lists.py`).

## Caching

Reads carry an `ETag` and are cached per viewer under scoped versions (`feed`, `post:<id>`, `user:<id>`, `people`,
`notif:<id>`) in Redis (memory if Redis is down); a write bumps only the scopes it changes. `If-None-Match` answers 304
while nothing a read depends on changed. Preview images: `Cache-Control: public, max-age=604800, immutable`.

## API

| | |
| --- | --- |
| `POST /session/challenge`, `POST /session {token, challenge, deviceKey, signature, author, codeHash}` | log in |
| `GET /me`, `PATCH /me {handle?, name?, bio?, shareActivity?}`, `GET /handles/<h>`, `POST /me/logout-all` | the person |
| `POST /posts {text}`, `GET/DELETE /posts/<id>`, `POST/DELETE /posts/<id>/like` | posts (masked; the link preview comes from the device, the server only checks it) |
| `GET/POST /posts/<id>/comments`, `DELETE /comments/<id>` | one level of comments |
| `GET /feed?before=&limit=` | everyone's posts + activity of people you follow, mixed (≤2 in a row per person), episodes grouped, suggestions while you follow fewer than 3 |
| `POST /activity {items}`, `GET /me/activity`, `PATCH /me/activity/<id> {hidden}` | what you watched |
| `GET /users/<id>`, `GET /people[?q=]`, `POST/DELETE /follow/<id>` | profiles, search, follows |
| `GET /notifications?since=`, `POST /notifications/read {upTo}` | comments, likes, followers |

## Licence

Apache License 2.0, like the rest of Kino: free to use, change and share. See [LICENSE](LICENSE).
