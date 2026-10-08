<#
.SYNOPSIS
  One-time setup for the Agent 365 3rd-party agent demo (laptop or VM).
  Installs Python 3.12, Ollama + model, Jaeger, creates .venv and .env.
#>
param(
    [string]$Model = "qwen2.5:7b",
    [string]$JaegerVersion = "2.21.0",
    [switch]$SkipOllama
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

Write-Host "==> Python 3.12" -ForegroundColor Cyan
$py312 = $false
try { py -3.12 --version | Out-Null; $py312 = $LASTEXITCODE -eq 0 } catch {}
if (-not $py312) {
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
}

Write-Host "==> Virtual environment + packages" -ForegroundColor Cyan
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q --upgrade pip
.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

if (-not $SkipOllama) {
    Write-Host "==> Ollama + $Model" -ForegroundColor Cyan
    $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (-not (Test-Path $ollama)) {
        winget install -e --id Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements
    }
    if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) {
        Start-Process $ollama -ArgumentList serve -WindowStyle Hidden
        Start-Sleep 5
    }
    & $ollama pull $Model
}

Write-Host "==> Jaeger $JaegerVersion (local trace viewer)" -ForegroundColor Cyan
$jdir = Join-Path $root "tools\jaeger"
if (-not (Test-Path "$jdir\jaeger.exe")) {
    New-Item -ItemType Directory -Force $jdir | Out-Null
    $zip = Join-Path $env:TEMP "jaeger.zip"
    Invoke-WebRequest "https://github.com/jaegertracing/jaeger/releases/download/v$JaegerVersion/jaeger-$JaegerVersion-windows-amd64.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $jdir -Force
    Get-ChildItem $jdir -Recurse -Filter jaeger.exe | Select-Object -First 1 | Move-Item -Destination "$jdir\jaeger.exe" -Force
    Remove-Item $zip
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    $secret = -join ((1..48) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
    (Get-Content .env) -replace '^SESSION_SECRET=.*', "SESSION_SECRET=$secret" | Set-Content .env
    Write-Host "Created .env (edit it after registration — see docs\02-register.md)" -ForegroundColor Yellow
}

Write-Host "`nSetup complete. Next: .\scripts\run.ps1" -ForegroundColor Green
