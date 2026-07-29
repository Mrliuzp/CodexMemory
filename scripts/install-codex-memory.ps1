[CmdletBinding()]
param(
    [string]$CodexCli = (Join-Path $env:APPDATA "npm\codex.cmd")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CodexHome = Join-Path $HOME ".codex"
$Runtime = Join-Path $CodexHome "codex-memory-runtime"
$SkillSource = Join-Path $RepoRoot "skills\codex-memory-auto-log"
$SkillTarget = Join-Path $CodexHome "skills\codex-memory-auto-log"
$HooksTemplate = Join-Path $RepoRoot "codex\hooks.global.json"
$HooksConfig = Join-Path $CodexHome "hooks.json"

function Backup-HooksConfig {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        $stamp = Get-Date -Format "yyyyMMddHHmmss"
        Copy-Item -LiteralPath $Path -Destination "$Path.$stamp.bak"
    }
}

function Merge-CodexMemoryHooks {
    param([string]$TemplatePath, [string]$ConfigPath)

    $template = Get-Content -LiteralPath $TemplatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (Test-Path -LiteralPath $ConfigPath) {
        $current = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } else {
        $current = [pscustomobject]@{ hooks = [pscustomobject]@{} }
    }
    if ($null -eq $current.hooks) {
        $current | Add-Member -MemberType NoteProperty -Name hooks -Value ([pscustomobject]@{}) -Force
    }

    Backup-HooksConfig -Path $ConfigPath
    foreach ($eventName in @("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")) {
        $property = $current.hooks.PSObject.Properties[$eventName]
        $existing = if ($null -eq $property) { @() } else { @($property.Value) }
        $retained = @($existing | Where-Object {
            (($_ | ConvertTo-Json -Depth 20 -Compress) -notmatch "codex-memory-runtime")
        })
        $replacement = @($template.hooks.PSObject.Properties[$eventName].Value)
        $current.hooks | Add-Member -MemberType NoteProperty -Name $eventName -Value @($retained + $replacement) -Force
    }

    $current | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
}

if (-not (Test-Path -LiteralPath $CodexCli)) {
    throw "未找到 Codex CLI：$CodexCli"
}

New-Item -ItemType Directory -Force -Path $CodexHome, (Join-Path $CodexHome "skills") | Out-Null
$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
$PythonExecutable = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $PythonLauncher) {
    & $PythonLauncher.Source -3 -m venv $Runtime
} elseif ($null -ne $PythonExecutable) {
    & $PythonExecutable.Source -m venv $Runtime
} else {
    throw "未找到 Python 解释器。请先安装 Python 3.10 或更高版本。"
}
if ($LASTEXITCODE -ne 0) { throw "创建 Codex Memory 运行环境失败" }
& (Join-Path $Runtime "Scripts\python.exe") -m pip install --upgrade $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "安装 Codex Memory 运行环境依赖失败" }

& $CodexCli mcp remove codex-memory 2>$null
& $CodexCli mcp add codex-memory --url "http://127.0.0.1:8001/mcp" --bearer-token-env-var CODEX_MEMORY_MCP_TOKEN
if ($LASTEXITCODE -ne 0) { throw "注册 Codex Memory MCP 失败" }

Copy-Item -LiteralPath $SkillSource -Destination $SkillTarget -Recurse -Force
Merge-CodexMemoryHooks -TemplatePath $HooksTemplate -ConfigPath $HooksConfig
Write-Host "Codex Memory 已安装。请重启 Codex 以加载 MCP、Skill 和 Hook。"
