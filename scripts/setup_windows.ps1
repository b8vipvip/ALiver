$ErrorActionPreference = "Stop"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher (py.exe) was not found. Install Python 3.10 or newer first."
}

$pythonVersion = $null
foreach ($version in @("3.12", "3.11", "3.10")) {
    & py "-$version" -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $pythonVersion = $version
        break
    }
}

if (-not $pythonVersion) {
    throw "ALiver requires Python 3.10 or newer. No supported Python version was found."
}

Write-Host "Using Python $pythonVersion" -ForegroundColor Cyan

if (Test-Path .\.venv) {
    Write-Host "Existing .venv detected; it will be reused." -ForegroundColor Yellow
} else {
    & py "-$pythonVersion" -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env. Please update ALIVER_SECRET_KEY before normal use." -ForegroundColor Yellow
}

Write-Host "Setup complete. Run .\scripts\run_windows.ps1 to start ALiver." -ForegroundColor Green
