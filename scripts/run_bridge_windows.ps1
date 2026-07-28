$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw ".venv was not found. Run .\scripts\setup_windows.ps1 first."
}

$logDir = Join-Path $projectRoot "bridge\logs\console"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$consoleLog = Join-Path $logDir "bridge-console-$stamp.log"

$env:PYTHONUNBUFFERED = "1"
$env:PYTHONFAULTHANDLER = "1"
$env:OPENCV_OPENCL_RUNTIME = "disabled"
$env:OMP_NUM_THREADS = "1"

Write-Host "Bridge 控制台日志：$consoleLog"
& $pythonExe -X faulthandler -u -m bridge.agent_sync *>&1 | Tee-Object -FilePath $consoleLog
$exitCode = $LASTEXITCODE

if ($null -eq $exitCode) {
    $exitCode = 1
}

if ($exitCode -ne 0) {
    Write-Host "Bridge 异常退出，正在自动生成故障包……" -ForegroundColor Yellow
    & $pythonExe -m bridge.runtime_diagnostics bundle `
        --reason "Bridge 进程异常退出" `
        --exit-code $exitCode `
        --console-log $consoleLog `
        --minutes 180
    Write-Host "请把 bridge\logs\bundles 中最新的 ZIP 文件发送给开发者。" -ForegroundColor Yellow
}

exit $exitCode
