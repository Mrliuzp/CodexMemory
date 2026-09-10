[CmdletBinding()]
param(
    [switch]$Run,
    [string]$TaskName = "Codex Memory 认证协调器",
    [string]$PythonExecutable = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CoordinatorTokenName = "CODEX_MEMORY_CODEX_AUTH_COORDINATOR_TOKEN"
$CoordinatorUrlName = "CODEX_MEMORY_CODEX_AUTH_COORDINATOR_URL"
$AuthDirectoryName = "CODEX_MEMORY_CODEX_CLI_AUTH_DIR"

function Get-ConfiguredValue {
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, "User")
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    }
    return $value
}

function Resolve-PythonExecutable {
    param([string]$ConfiguredPath)

    if (-not [string]::IsNullOrWhiteSpace($ConfiguredPath)) {
        if (-not (Test-Path -LiteralPath $ConfiguredPath -PathType Leaf)) {
            throw "未找到 Python 解释器：$ConfiguredPath"
        }
        return (Resolve-Path -LiteralPath $ConfiguredPath).Path
    }

    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "未找到 Python 解释器。"
    }
    return $command.Source
}

function Start-AuthCoordinator {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    $token = Get-ConfiguredValue -Name $CoordinatorTokenName
    $authDirectory = Get-ConfiguredValue -Name $AuthDirectoryName
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "$CoordinatorTokenName 未配置。"
    }
    if ([string]::IsNullOrWhiteSpace($authDirectory) -or -not (Test-Path -LiteralPath $authDirectory -PathType Container)) {
        throw "$AuthDirectoryName 未配置或目录不存在。"
    }

    $env:CODEX_MEMORY_CODEX_AUTH_COORDINATOR_TOKEN = $token
    $sourceRoot = Join-Path $RepoRoot "src"
    $existingPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
    $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($existingPythonPath)) {
        $sourceRoot
    } else {
        "$sourceRoot$([IO.Path]::PathSeparator)$existingPythonPath"
    }
    Set-Location -LiteralPath $RepoRoot
    & $PythonPath -m codex_memory.codex_auth `
        --auth-root $authDirectory `
        --bind 127.0.0.1 `
        --port 1456
    exit $LASTEXITCODE
}

$ResolvedPython = Resolve-PythonExecutable -ConfiguredPath $PythonExecutable
if ($Run) {
    Start-AuthCoordinator -PythonPath $ResolvedPython
}

$requiredValues = @{
    $CoordinatorTokenName = Get-ConfiguredValue -Name $CoordinatorTokenName
    $AuthDirectoryName = Get-ConfiguredValue -Name $AuthDirectoryName
}
foreach ($entry in $requiredValues.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace($entry.Value)) {
        throw "$($entry.Key) 未配置，无法安装自启动任务。"
    }
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "User")
}
if (-not (Test-Path -LiteralPath $requiredValues[$AuthDirectoryName] -PathType Container)) {
    throw "$AuthDirectoryName 指向的目录不存在。"
}

$coordinatorUrl = Get-ConfiguredValue -Name $CoordinatorUrlName
if ([string]::IsNullOrWhiteSpace($coordinatorUrl)) {
    $coordinatorUrl = "http://host.docker.internal:1456"
}
[Environment]::SetEnvironmentVariable($CoordinatorUrlName, $coordinatorUrl, "User")

$PowerShellExecutable = (Get-Process -Id $PID -ErrorAction Stop).Path
$escapedScriptPath = $PSCommandPath.Replace('"', '`"')
$escapedPythonPath = $ResolvedPython.Replace('"', '`"')
$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$escapedScriptPath`" -Run -PythonExecutable `"$escapedPythonPath`""
$action = New-ScheduledTaskAction `
    -Execute $PowerShellExecutable `
    -Argument $actionArguments `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "用户登录后自动启动 Codex Memory 认证协调器，仅监听 127.0.0.1:1456。" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "已注册并启动计划任务：$TaskName"
