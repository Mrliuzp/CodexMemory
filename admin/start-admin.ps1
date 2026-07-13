# Codex Memory Admin Dashboard
# Quick start script

Write-Host "Starting Codex Memory Admin Dashboard..." -ForegroundColor Cyan

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv" "Scripts" "python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Virtual environment Python not found at: $VenvPython" -ForegroundColor Yellow
    Write-Host "Trying system Python..." -ForegroundColor Yellow
    $PythonExe = "python"
} else {
    $PythonExe = $VenvPython
}

# Change to project root so relative DB path works
Push-Location $ProjectRoot

try {
    & $PythonExe -m uvicorn admin.main:app --host 127.0.0.1 --port 8500 --reload
} finally {
    Pop-Location
}
