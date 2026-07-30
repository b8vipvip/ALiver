param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tool = Join-Path $root "tools\douyin_live_collector"
$out = Join-Path $tool "dist"

if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python -m pip install --quiet --upgrade pyinstaller
Push-Location $tool
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --console `
        --name ALiverDouyinCollector `
        collector.py
    Copy-Item "douyin_collector.example.json" (Join-Path $out "douyin_collector.example.json") -Force
    Write-Host "Built: $out\ALiverDouyinCollector.exe" -ForegroundColor Green
} finally {
    Pop-Location
}
