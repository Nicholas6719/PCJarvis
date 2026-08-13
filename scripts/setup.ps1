# ══════════════════════════════════════════════════════════════════
#  J.A.R.V.I.S. setup.  Idempotent -- safe to re-run at any point.
# ══════════════════════════════════════════════════════════════════
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "   $msg" -ForegroundColor DarkGray }

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host " J.A.R.V.I.S. setup" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# ── 1. system packages ────────────────────────────────────────────
Step "Installing system packages via winget"
$packages = @(
    @{ id = "Python.Python.3.12";          name = "Python 3.12" },
    @{ id = "Ollama.Ollama";               name = "Ollama" },
    @{ id = "Gyan.FFmpeg";                 name = "ffmpeg" },
    # onnxruntime and ctranslate2 are native DLLs; without this they fail to
    # load with a bare "module not found", which is a miserable thing to debug.
    @{ id = "Microsoft.VCRedist.2015+.x64"; name = "Visual C++ runtime" }
)
foreach ($p in $packages) {
    Ok "$($p.name) ..."
    winget install --id $p.id -e --accept-package-agreements `
        --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
}

# ── 2. virtual environment ────────────────────────────────────────
Step "Creating the virtual environment"
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
if (-not (Test-Path ".venv")) { & $python -m venv .venv }
$venvPy = ".\.venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip --quiet
Ok "installing dependencies (this takes a few minutes)"
& $venvPy -m pip install -r requirements.txt --quiet

# ── 3. Ollama + iGPU ──────────────────────────────────────────────
Step "Configuring Ollama"
# The Radeon 780M is detected but dropped by default because it is integrated.
# Enabling it moves inference from 100% CPU to 100% GPU.
[Environment]::SetEnvironmentVariable("OLLAMA_IGPU_ENABLE", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "30m", "User")
$env:OLLAMA_IGPU_ENABLE = "1"
Ok "integrated GPU enabled"

$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

Step "Pulling the language model (~4.7 GB)"
& $ollama pull qwen2.5:7b-instruct

# ── 4. voice + hearing models ─────────────────────────────────────
Step "Downloading wake word, VAD, speech and voice models (~850 MB)"
& $venvPy scripts\download_models.py

# ── 5. verify ─────────────────────────────────────────────────────
Step "Verifying"
& $venvPy -c @"
import sys
try:
    import onnxruntime, ctranslate2, sounddevice, webview
    from jarvis.tools import registry
    n = registry.load_all()
    print(f'   {n} tools registered')
    print('   all runtimes load')
except Exception as e:
    print(f'   FAILED: {e}'); sys.exit(1)
"@

Write-Host "`n=========================================" -ForegroundColor Green
Write-Host " Ready.  Start with:  .\run_jarvis.ps1" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green
