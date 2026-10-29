# Starts the API and the Cloudflare tunnel in the background, logs in .\logs. Run at logon by the "KinoSocial" task.
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force logs | Out-Null
$api = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -match 'uvicorn app.main:app' }
if (-not $api) {
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\run.ps1`"" `
        -RedirectStandardOutput logs\api.out.log -RedirectStandardError logs\api.err.log
}
$tunnel = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" | Where-Object { $_.CommandLine -match 'kino-social|cloudflared.yml' }
if (-not $tunnel) {
    Start-Process "C:\Program Files (x86)\cloudflared\cloudflared.exe" -WindowStyle Hidden `
        -ArgumentList "tunnel --config `"$PSScriptRoot\cloudflared.yml`" run kino-social" `
        -RedirectStandardOutput logs\tunnel.out.log -RedirectStandardError logs\tunnel.err.log
}
