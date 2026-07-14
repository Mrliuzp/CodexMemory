[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Secure = Read-Host "请输入 CODEX_MEMORY_MCP_TOKEN" -AsSecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
try {
    $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    if ([string]::IsNullOrWhiteSpace($Plain) -or $Plain.StartsWith("change-me", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Token 不能为空或使用占位符"
    }
    [Environment]::SetEnvironmentVariable("CODEX_MEMORY_MCP_TOKEN", $Plain, "User")
    Write-Host "CODEX_MEMORY_MCP_TOKEN 已写入当前用户环境变量。请重启 Codex。"
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    Remove-Variable Plain -ErrorAction SilentlyContinue
}