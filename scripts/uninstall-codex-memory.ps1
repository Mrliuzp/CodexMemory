[CmdletBinding()]
param(
    [switch]$RemoveToken,
    [string]$CodexCli = (Join-Path $env:APPDATA "npm\codex.cmd")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CodexHome = Join-Path $HOME ".codex"
$Runtime = Join-Path $CodexHome "codex-memory-runtime"
$SkillTarget = Join-Path $CodexHome "skills\codex-memory-auto-log"
$HooksConfig = Join-Path $CodexHome "hooks.json"

function Backup-HooksConfig {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        $stamp = Get-Date -Format "yyyyMMddHHmmss"
        Copy-Item -LiteralPath $Path -Destination "$Path.$stamp.bak"
    }
}

function Remove-CodexMemoryHooks {
    param([string]$ConfigPath)
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return }

    $current = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $current.hooks) { return }
    $changed = $false
    foreach ($eventName in @("UserPromptSubmit", "Stop")) {
        $property = $current.hooks.PSObject.Properties[$eventName]
        if ($null -eq $property) { continue }
        $remaining = @($property.Value | Where-Object {
            (($_ | ConvertTo-Json -Depth 20 -Compress) -notmatch "codex-memory-runtime")
        })
        if ($remaining.Count -ne @($property.Value).Count) {
            $changed = $true
            if ($remaining.Count -eq 0) {
                $current.hooks.PSObject.Properties.Remove($eventName)
            } else {
                $current.hooks | Add-Member -MemberType NoteProperty -Name $eventName -Value $remaining -Force
            }
        }
    }
    if ($changed) {
        Backup-HooksConfig -Path $ConfigPath
        $current | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
    }
}

if (Test-Path -LiteralPath $CodexCli) {
    & $CodexCli mcp remove codex-memory 2>$null
}
Remove-CodexMemoryHooks -ConfigPath $HooksConfig
if (Test-Path -LiteralPath $SkillTarget) {
    Remove-Item -LiteralPath $SkillTarget -Recurse -Force
}
if (Test-Path -LiteralPath $Runtime) {
    Remove-Item -LiteralPath $Runtime -Recurse -Force
}
# 仅在显式传入 -RemoveToken 时删除用户级 Token。
if ($RemoveToken) {
    [Environment]::SetEnvironmentVariable("CODEX_MEMORY_MCP_TOKEN", $null, "User")
}
Write-Host "Codex Memory 已卸载。"