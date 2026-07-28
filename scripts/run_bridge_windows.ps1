$ErrorActionPreference = "Stop"

$pythonExe = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw ".venv was not found. Run .\scripts\setup_windows.ps1 first."
}

& $pythonExe -m bridge.agent_sync
