param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Command
)

if ($null -eq $Command) {
    $Command = @()
}
else {
    $Command = @($Command)
}

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$srcPath = Join-Path $root "src"
$installStamp = Join-Path $root ".venv\.codex-install.stamp"
$defaultHost = if ($env:CODEX_MEMORY_HTTP_HOST) { $env:CODEX_MEMORY_HTTP_HOST } else { "127.0.0.1" }
$defaultPort = if ($env:CODEX_MEMORY_HTTP_PORT) { [int]$env:CODEX_MEMORY_HTTP_PORT } else { 8000 }

if ($env:PYTHONPATH) {
    if ($env:PYTHONPATH -notlike "*$srcPath*") {
        $env:PYTHONPATH = "$srcPath;$env:PYTHONPATH"
    }
}
else {
    $env:PYTHONPATH = $srcPath
}

function Ensure-LocalPython {
    $runtimePython = Join-Path $root ".codex-python\pkg\tools\python.exe"
    if (Test-Path $venvPython) {
        return
    }

    if (-not (Test-Path $runtimePython)) {
        $runtimeDir = Join-Path $root ".codex-python"
        $nupkg = Join-Path $runtimeDir "python.nupkg"
        $zip = Join-Path $runtimeDir "python.zip"
        $pkg = Join-Path $runtimeDir "pkg"

        New-Item -ItemType Directory -Force $runtimeDir | Out-Null
        Invoke-WebRequest -Uri "https://www.nuget.org/api/v2/package/python/3.12.10" -OutFile $nupkg
        Copy-Item $nupkg $zip -Force
        Expand-Archive -Force $zip $pkg
    }

    & $runtimePython -m venv .venv
}

function Get-LatestSourceTime {
    $latest = Get-Date "1970-01-01Z"
    $paths = @(
        (Join-Path $root "pyproject.toml"),
        (Join-Path $root "src"),
        (Join-Path $root "tests"),
        (Join-Path $root "tools")
    )

    foreach ($path in $paths) {
        if (-not (Test-Path $path)) {
            continue
        }

        if ((Get-Item -LiteralPath $path).PSIsContainer) {
            $items = Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue
        }
        else {
            $items = @((Get-Item -LiteralPath $path))
        }

        foreach ($item in $items) {
            if ($item.LastWriteTimeUtc -gt $latest) {
                $latest = $item.LastWriteTimeUtc
            }
        }
    }

    return $latest
}

function Install-Project {
    $latestSourceTime = Get-LatestSourceTime
    $needsInstall = -not (Test-Path $installStamp)

    if (-not $needsInstall) {
        $stampTime = (Get-Item -LiteralPath $installStamp).LastWriteTimeUtc
        if ($latestSourceTime -gt $stampTime) {
            $needsInstall = $true
        }
    }

    if ($needsInstall) {
        & $venvPython -m pip install -e .
        Set-Content -LiteralPath $installStamp -Value (Get-Date).ToString("o") -Encoding ASCII
    }
}

function Show-Usage {
    Write-Host ""
    Write-Host "Local commands:"
    Write-Host "  .\start-local.ps1 health"
    Write-Host "  .\start-local.ps1 append --project demo --conversation c1 --role user --content 'Bug: auth token refresh throws'"
    Write-Host "  .\start-local.ps1 context --project demo --task 'Fix auth token refresh'"
    Write-Host ""
}

Ensure-LocalPython
Install-Project

if ($Command.Count -eq 0) {
    $serviceUrl = "http://$defaultHost`:$defaultPort"
    $listener = Get-NetTCPConnection -State Listen -LocalPort $defaultPort -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -eq $defaultHost -or $_.LocalAddress -eq "0.0.0.0" -or $_.LocalAddress -eq "::" }

    if ($listener) {
        Write-Host "Codex Memory HTTP service is already listening at $serviceUrl"
        Write-Host "Use the browser tab or call the service directly; console logs are only available from the process that started it."
        exit 0
    }

    Write-Host "Starting Codex Memory HTTP service at $serviceUrl"
    Write-Host "Request logs will stream to this console."
    & $venvPython -m codex_memory.cli serve --host $defaultHost --port $defaultPort
    exit $LASTEXITCODE
}

& $venvPython -m codex_memory.cli @Command
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
