# Starts the API on 127.0.0.1:8787 (the Cloudflare tunnel is the only way in). Its DSN, Redis URL and allowed plugin
# authors come from data\pg.env (KINO_SOCIAL_DSN, KINO_SOCIAL_REDIS, KINO_SOCIAL_AUTHORS); never committed.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force data, logs | Out-Null
$env:KINO_SOCIAL_SERVE = "1"
$env:KINO_SOCIAL_PUBLIC_URL = "https://social.comparadorinternet.co"
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --proxy-headers --forwarded-allow-ips 127.0.0.1
