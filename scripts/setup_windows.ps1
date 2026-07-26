$ErrorActionPreference = "Stop"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "未找到 Python Launcher（py）。请先安装 Python 3.11 或 3.12。"
}

py -3.11 -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "已创建 .env，请修改 ALIVER_SECRET_KEY。" -ForegroundColor Yellow
}

Write-Host "安装完成。执行 .\scripts\run_windows.ps1 启动。" -ForegroundColor Green
