# Build the standalone JARVIS.exe, stage its runtime files, make shortcuts.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".\.venv\Scripts\python.exe"

Write-Host "`n>> Icon" -ForegroundColor Cyan
& $py scripts\make_icon.py

Write-Host "`n>> Building (a few minutes)" -ForegroundColor Cyan
& $py -m PyInstaller JARVIS.spec --noconfirm --clean --log-level WARN

Write-Host "`n>> Staging runtime files beside the exe" -ForegroundColor Cyan
# These live next to the exe rather than inside it so they stay editable and
# so models can be updated without a rebuild.
Copy-Item config.yaml dist\JARVIS\ -Force
New-Item -ItemType Directory -Force dist\JARVIS\assets | Out-Null
Copy-Item assets\jarvis.ico dist\JARVIS\assets\ -Force
if (-not (Test-Path dist\JARVIS\models)) {
    Write-Host "   copying models (~880 MB, one time)" -ForegroundColor DarkGray
    Copy-Item models dist\JARVIS\ -Recurse -Force
}

& "$PSScriptRoot\install_shortcuts.ps1"

$size = "{0:N0}" -f ((Get-ChildItem dist\JARVIS -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "`nBuilt dist\JARVIS\JARVIS.exe  ($size MB total)" -ForegroundColor Green
