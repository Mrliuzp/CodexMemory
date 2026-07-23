import json
from pathlib import Path


def test_skill_requires_agents_activation_marker() -> None:
    content = Path("skills/codex-memory-auto-log/SKILL.md").read_text(encoding="utf-8")

    assert "CODEX_MEMORY_AUTO_LOG=required" in content
    assert "未启用项目不得自动写入" in content
    assert "append_message" in content
    assert "build_context" in content


def test_skill_allows_implicit_invocation() -> None:
    metadata = Path(
        "skills/codex-memory-auto-log/agents/openai.yaml"
    ).read_text(encoding="utf-8")

    assert "allow_implicit_invocation: true" in metadata

def test_global_hook_calls_installed_runtime() -> None:
    hooks = json.loads(Path("codex/hooks.global.json").read_text(encoding="utf-8"))

    assert "UserPromptSubmit" in hooks["hooks"]
    assert "Stop" in hooks["hooks"]

def test_installer_registers_http_mcp_without_embedding_token() -> None:
    content = Path("scripts/install-codex-memory.ps1").read_text(encoding="utf-8")

    assert "mcp remove codex-memory" in content
    assert "mcp add codex-memory" in content
    assert "--url" in content
    assert "http://127.0.0.1:8001/mcp" in content
    assert "--bearer-token-env-var" in content
    assert "CODEX_MEMORY_MCP_TOKEN" in content
    assert "Set-Content $CodexConfig" not in content


def test_token_setter_uses_secure_prompt_and_user_environment() -> None:
    content = Path("scripts/set-codex-memory-token.ps1").read_text(encoding="utf-8")

    assert 'Read-Host "请输入 CODEX_MEMORY_MCP_TOKEN" -AsSecureString' in content
    assert "SetEnvironmentVariable(\"CODEX_MEMORY_MCP_TOKEN\", $Plain, \"User\")" in content
    assert "change-me" in content
    assert "Write-Output $Plain" not in content


def test_uninstaller_only_removes_project_owned_components() -> None:
    content = Path("scripts/uninstall-codex-memory.ps1").read_text(encoding="utf-8")

    assert "mcp remove codex-memory" in content
    assert "codex-memory-runtime" in content
    assert "-RemoveToken" in content
    assert "codex-memory-auto-log" in content

def test_windows_powershell_scripts_use_utf8_bom() -> None:
    for name in (
        "install-codex-memory.ps1",
        "uninstall-codex-memory.ps1",
        "set-codex-memory-token.ps1",
    ):
        assert Path("scripts", name).read_bytes().startswith(b"\xef\xbb\xbf")