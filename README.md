# Kino Social backend

The server of the Kino Social plugin (an experiment; the one plugin Kino's owner allowed a backend for, 2026-10-28).
FastAPI + SQLite on this PC, `127.0.0.1:8787`, reached only through the Cloudflare tunnel `kino-social`
(`d3dfd239-fa2d-488f-8878-12b8f834642a`) at **https://social.comparadorinternet.co**.

## Run

```powershell
.\start-all.ps1          # API + tunnel in the background, logs in .\logs
.\run.ps1                # API only, in the foreground
.\.venv\Scripts\python.exe -m pytest -q
```

Stop: end the `python.exe` (uvicorn) and the `cloudflared.exe` running `kino-social` in Task Manager.

Start at logon (not registered automatically; run it yourself if you want it):

```powershell
$a = New-ScheduledTaskAction -Execute powershell.exe -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\dev\kino-social-backend\start-all.ps1"
Register-ScheduledTask -TaskName KinoSocial -Action $a -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Force
```

## Data

`data\social.db` (SQLite, WAL). Delete it to start from zero. Preview images live in its `media` table.

## Identity

`POST /session {token}`: Kino's gate token (Ed25519, signed by @kinocodigo_bot), handed to the plugin by
`kino.identity.telegram()`. The token never expires and cannot be tied to a device here: a leaked token can post as its
owner. Fine for the experiment; a per-audience expiring token from the bot is the fix if this grows.

## API

All JSON, `Authorization: Bearer <session>` except `/session`, `/health` and `/media/<id>`.
`GET /feed?before=&limit=` and `GET /posts/<id>/comments` send an `ETag`; `If-None-Match` gets a 304 while nothing changed.

| | |
| --- | --- |
| `GET /me`, `PATCH /me {name?, bio?, shareActivity?}` | own profile |
| `POST /posts {text}`, `DELETE /posts/<id>` | post (masked, link preview) |
| `GET/POST /posts/<id>/comments`, `DELETE /comments/<id>` | one level of comments |
| `GET /feed` | posts by everyone + activity of people you follow |
| `POST /activity {items}`, `GET /me/activity`, `PATCH /me/activity/<id> {hidden}` | what you watched |
| `GET /users/<id>`, `GET /people`, `POST/DELETE /follow/<id>` | profiles and follows |
| `GET /notifications?since=`, `POST /notifications/read` | comments on your posts, new followers |
