# Starts, in the background: PostgreSQL + Redis inside WSL Ubuntu (kept attached so WSL never idles them away), the API on
# 127.0.0.1:8787, and the Cloudflare tunnel. Logs in .\logs. Safe to run again: it starts only what is not running.
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force logs | Out-Null
$procs = Get-CimInstance Win32_Process
if (-not ($procs | Where-Object { $_.Name -eq 'wsl.exe' -and $_.CommandLine -match 'wsl-services.sh' })) {
    Start-Process wsl.exe -WindowStyle Hidden -ArgumentList "-d Ubuntu -u root -e sh /mnt/c/dev/kino-social-backend/scripts/wsl-services.sh" `
        -RedirectStandardOutput logs\wsl.out.log -RedirectStandardError logs\wsl.err.log
    Start-Sleep -Seconds 5
}
if (-not ($procs | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'uvicorn app.main:app' })) {
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\run.ps1`"" `
        -RedirectStandardOutput logs\api.out.log -RedirectStandardError logs\api.err.log
}
if (-not ($procs | Where-Object { $_.Name -eq 'cloudflared.exe' -and $_.CommandLine -match 'cloudflared.yml' })) {
    Start-Process "C:\Program Files (x86)\cloudflared\cloudflared.exe" -WindowStyle Hidden `
        -ArgumentList "tunnel --config `"$PSScriptRoot\cloudflared.yml`" run kino-social" `
        -RedirectStandardOutput logs\tunnel.out.log -RedirectStandardError logs\tunnel.err.log
}
