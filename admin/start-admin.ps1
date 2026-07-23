# Codex Memory 管理后台
# 快速启动脚本

Write-Host "正在启动 Codex Memory 管理后台……" -ForegroundColor Cyan

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv" "Scripts" "python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "未找到虚拟环境 Python： $VenvPython" -ForegroundColor Yellow
    Write-Host "Trying system Python..." -ForegroundColor Yellow
    $PythonExe = "python"
} else {
    $PythonExe = $VenvPython
}

# 切换到项目根目录，确保相对数据库路径可用
Push-Location $ProjectRoot

try {
    & $PythonExe -m uvicorn admin.main:app --host 127.0.0.1 --port 8500 --reload
} finally {
    Pop-Location
}
