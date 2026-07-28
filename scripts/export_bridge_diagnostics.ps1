$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw ".venv was not found. Run .\scripts\setup_windows.ps1 first."
}

& $pythonExe -m bridge.runtime_diagnostics bundle --reason "用户手动导出" --minutes 180
Write-Host "请把 bridge\logs\bundles 中最新的 ZIP 文件发送给开发者。" -ForegroundColor Green
