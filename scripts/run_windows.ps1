$ErrorActionPreference = "Stop"

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    throw "未找到 .venv。请先执行 .\scripts\setup_windows.ps1"
}

& .\.venv\Scripts\Activate.ps1
python -m app.main
