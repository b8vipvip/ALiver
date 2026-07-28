$ErrorActionPreference = "Stop"

$matches = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -and
    ($_.CommandLine -match 'bridge\.agent_sync' -or $_.CommandLine -match 'bridge[\\/]agent_sync\.py')
}

if (-not $matches) {
    Write-Host "没有发现正在运行的 ALiver Bridge 进程。" -ForegroundColor Green
    exit 0
}

foreach ($process in $matches) {
    Write-Host "正在停止 ALiver Bridge：PID=$($process.ProcessId)" -ForegroundColor Yellow
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 700
Write-Host "ALiver Bridge 进程已停止。" -ForegroundColor Green
