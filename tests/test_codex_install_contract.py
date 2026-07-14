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
