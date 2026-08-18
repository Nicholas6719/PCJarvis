# Build the standalone JARVIS.exe, stage its runtime files, make shortcuts.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".\.venv\Scripts\python.exe"

Write-Host "`n>> Icon" -ForegroundColor Cyan
& $py scripts\make_icon.py

# Windows keeps a handle on the runtime DLLs after JARVIS exits -- long
# enough that PyInstaller --clean fails deleting dist\JARVIS, and it fails
# HALFWAY, leaving a 39 MB stub where a working 1.4 GB app used to be. That
# happened three times before this was worth automating.
#
# Renaming the folder always succeeds where deleting it does not, so the
# build gets a clean directory and the locked copy is swept up on a later
# run once Windows has let go.
if (Test-Path "dist\JARVIS") {
    try {
        Remove-Item -Recurse -Force "dist\JARVIS" -ErrorAction Stop
    } catch {
        $stale = "JARVIS_stale_" + (Get-Date -Format "yyyyMMdd_HHmmss")
        Rename-Item "dist\JARVIS" $stale
        Write-Host "   previous build was locked; moved aside as $stale" -ForegroundColor DarkYellow
    }
}
Get-ChildItem "dist" -Directory -Filter "JARVIS_stale_*" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }

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
