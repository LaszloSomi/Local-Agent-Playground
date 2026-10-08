<#
.SYNOPSIS
  Start Jaeger (local trace UI), make sure Ollama is running, then start the agent web app.
.EXAMPLE
  .\scripts\run.ps1                          # agent 1 on :8000 (.env)
  .\scripts\run.ps1 -EnvFile .env.agent2     # agent 2 (same blueprint) on the port in .env.agent2
#>
param([switch]$NoJaeger, [string]$EnvFile)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$port = 8000
if ($EnvFile) {
    $EnvFile = (Resolve-Path $EnvFile).Path
    $env:AGENT_ENV_FILE = $EnvFile
    $p = (Select-String -Path $EnvFile -Pattern '^APP_PORT=(\d+)' -ErrorAction SilentlyContinue).Matches.Groups[1].Value
    if ($p) { $port = [int]$p }
    Write-Host "Instance overlay: $EnvFile (shared values from .env)" -ForegroundColor Cyan
} else {
    Remove-Item Env:AGENT_ENV_FILE -ErrorAction SilentlyContinue
}

if (-not $NoJaeger) {
    $jaeger = Join-Path $root "tools\jaeger\jaeger.exe"
    if ((Test-Path $jaeger) -and -not (Get-Process jaeger -ErrorAction SilentlyContinue)) {
        Start-Process $jaeger -WindowStyle Hidden -RedirectStandardOutput "$root\tools\jaeger\jaeger.log" -RedirectStandardError "$root\tools\jaeger\jaeger.err.log"
        Write-Host "Jaeger UI: http://localhost:16686  (OTLP/HTTP on :4318)" -ForegroundColor Cyan
    }
}

$provider = (Select-String -Path .env -Pattern '^LLM_PROVIDER=(.*)$' -ErrorAction SilentlyContinue).Matches.Groups[1].Value
if ($provider -ne 'github' -and -not (Get-Process ollama -ErrorAction SilentlyContinue)) {
    Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" -ArgumentList serve -WindowStyle Hidden
    Start-Sleep 3
}

$busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($busy) {
    $p = Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue
    throw "Port $port is already in use by $($p.ProcessName) (PID $($busy.OwningProcess)) - stop it first:  Stop-Process -Id $($busy.OwningProcess)"
}

Write-Host "Agent UI:  http://localhost:$port" -ForegroundColor Green
.\.venv\Scripts\python.exe -m agent.app
