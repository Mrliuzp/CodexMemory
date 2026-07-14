from pathlib import Path

import pytest

from codex_memory.project_config import (
    ProjectConfigError,
    find_agents_file,
    load_project_memory_config,
)


def write_agents(directory: Path, content: str, *, encoding: str = "utf-8") -> Path:
    agents_file = directory / "AGENTS.md"
    agents_file.write_text(content, encoding=encoding)
    return agents_file


def enabled_settings(project_id: str = "erp-backend") -> str:
    return (
        "CODEX_MEMORY_AUTO_LOG=required\n"
        f"CODEX_MEMORY_PROJECT_ID={project_id}\n"
        "CODEX_MEMORY_MCP_SERVER=codex-memory\n"
    )


def test_loads_enabled_project_from_nearest_agents_file(tmp_path: Path) -> None:
    project = tmp_path / "erp"
    nested = project / "src" / "orders"
    nested.mkdir(parents=True)
    agents_file = write_agents(project, "# Constraints\n" + enabled_settings())

    config = load_project_memory_config(nested)

    assert config.enabled is True
    assert config.project_id == "erp-backend"
    assert config.mcp_server == "codex-memory"
    assert config.agents_file == agents_file


def test_nearest_agents_file_takes_precedence(tmp_path: Path) -> None:
    project = tmp_path / "erp"
    nested = project / "src" / "orders"
    nested.mkdir(parents=True)
    write_agents(project, enabled_settings("outer-project"))
    nearest_agents_file = write_agents(nested.parent, enabled_settings("inner-project"))

    config = load_project_memory_config(nested)

    assert config.project_id == "inner-project"
    assert config.agents_file == nearest_agents_file
    assert find_agents_file(nested) == nearest_agents_file


def test_utf8_bom_is_accepted(tmp_path: Path) -> None:
    agents_file = write_agents(tmp_path, enabled_settings(), encoding="utf-8-sig")

    config = load_project_memory_config(tmp_path)

    assert config.enabled is True
    assert config.agents_file == agents_file


@pytest.mark.parametrize(
    "content",
    [
        "# Ordinary constraints\n",
        "CODEX_MEMORY_AUTO_LOG=disabled\n",
        "CODEX_MEMORY_AUTO_LOG=optional\n",
    ],
)
def test_missing_disabled_or_unknown_marker_is_disabled(tmp_path: Path, content: str) -> None:
    agents_file = write_agents(tmp_path, content)

    config = load_project_memory_config(tmp_path)

    assert config.enabled is False
    assert config.project_id is None
    assert config.mcp_server is None
    assert config.agents_file == agents_file


def test_without_agents_file_is_disabled(tmp_path: Path) -> None:
    config = load_project_memory_config(tmp_path)

    assert config.enabled is False
    assert config.project_id is None
    assert config.mcp_server is None
    assert config.agents_file is None


@pytest.mark.parametrize(
    "setting, value",
    [
        ("CODEX_MEMORY_PROJECT_ID", "ERP Chinese"),
        ("CODEX_MEMORY_MCP_SERVER", "Codex-Memory"),
    ],
)
def test_required_rejects_invalid_identifier(
    tmp_path: Path, setting: str, value: str
) -> None:
    settings = enabled_settings().replace(f"{setting}=" + (
        "erp-backend" if setting.endswith("PROJECT_ID") else "codex-memory"
    ), f"{setting}={value}")
    write_agents(tmp_path, settings)

    with pytest.raises(ProjectConfigError, match=rf"{setting} 格式无效"):
        load_project_memory_config(tmp_path)


def test_required_rejects_project_id_longer_than_64_characters(tmp_path: Path) -> None:
    write_agents(tmp_path, enabled_settings("a" * 65))

    with pytest.raises(ProjectConfigError, match="PROJECT_ID"):
        load_project_memory_config(tmp_path)


def test_duplicate_settings_with_same_value_are_accepted(tmp_path: Path) -> None:
    write_agents(
        tmp_path,
        enabled_settings() + "CODEX_MEMORY_PROJECT_ID=erp-backend\n",
    )

    config = load_project_memory_config(tmp_path)

    assert config.enabled is True
    assert config.project_id == "erp-backend"


def test_duplicate_settings_with_conflicting_values_are_rejected(tmp_path: Path) -> None:
    write_agents(
        tmp_path,
        enabled_settings() + "CODEX_MEMORY_PROJECT_ID=warehouse\n",
    )

    with pytest.raises(ProjectConfigError, match="参数 CODEX_MEMORY_PROJECT_ID 的值冲突"):
        load_project_memory_config(tmp_path)
