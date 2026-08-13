# Create Desktop and Start Menu shortcuts for the built app.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$exe  = Join-Path $root "dist\JARVIS\JARVIS.exe"
$icon = Join-Path $root "dist\JARVIS\assets\jarvis.ico"

if (-not (Test-Path $exe)) {
    Write-Host "Build it first:  .\scripts\build_app.ps1" -ForegroundColor Red
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$targets = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs")
)

foreach ($dir in $targets) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "JARVIS.lnk"))
    $lnk.TargetPath       = $exe
    $lnk.WorkingDirectory = Split-Path $exe -Parent
    $lnk.IconLocation     = "$icon,0"
    $lnk.Description      = "J.A.R.V.I.S. - local voice assistant"
    $lnk.Save()
    Write-Host "  created $(Join-Path $dir 'JARVIS.lnk')" -ForegroundColor DarkGray
}

Write-Host "Shortcuts installed." -ForegroundColor Green
