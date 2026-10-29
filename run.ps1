# Starts the API on 127.0.0.1:8787 (the Cloudflare tunnel is the only way in).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force data, logs | Out-Null
$env:KINO_SOCIAL_SERVE = "1"
$env:KINO_SOCIAL_DB = "data\social.db"
$env:KINO_SOCIAL_PUBLIC_URL = "https://social.comparadorinternet.co"
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --proxy-headers
