# J.A.R.V.I.S. launcher
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "No virtual environment found. Run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

# Ollama must be up, and must be allowed to use the integrated GPU.
$env:OLLAMA_IGPU_ENABLE       = "1"
$env:OLLAMA_KEEP_ALIVE        = "15m"
$env:OLLAMA_MAX_LOADED_MODELS = "1"
$env:OLLAMA_NUM_PARALLEL      = "1"
$env:OLLAMA_FLASH_ATTENTION   = "1"
$env:OLLAMA_KV_CACHE_TYPE     = "q8_0"

if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Starting Ollama..." -ForegroundColor DarkGray
    Start-Process -FilePath "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" `
                  -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

& $py -m jarvis.main @args
