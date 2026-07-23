import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SETTING_PATTERN = re.compile(r"^(CODEX_MEMORY_[A-Z_]+)=(.*)$")


class ProjectConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectMemoryConfig:
    enabled: bool
    project_id: str | None
    mcp_server: str | None
    agents_file: Path | None


def find_agents_file(cwd: str | Path) -> Path | None:
    current = Path(cwd).resolve()
    while True:
        agents_file = current / "AGENTS.md"
        if agents_file.is_file():
            return agents_file
        if current.parent == current:
            return None
        current = current.parent


def load_project_memory_config(cwd: str | Path) -> ProjectMemoryConfig:
    agents_file = find_agents_file(cwd)
    if agents_file is None:
        return ProjectMemoryConfig(False, None, None, None)

    values = _parse_settings(agents_file.read_text(encoding="utf-8-sig"))
    if values.get("CODEX_MEMORY_AUTO_LOG", "disabled") != "required":
        return ProjectMemoryConfig(False, None, None, agents_file)

    project_id = values.get("CODEX_MEMORY_PROJECT_ID", "")
    mcp_server = values.get("CODEX_MEMORY_MCP_SERVER", "")
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ProjectConfigError("CODEX_MEMORY_PROJECT_ID 格式无效")
    if not PROJECT_ID_PATTERN.fullmatch(mcp_server):
        raise ProjectConfigError("CODEX_MEMORY_MCP_SERVER 格式无效")
    return ProjectMemoryConfig(True, project_id, mcp_server, agents_file)


def _parse_settings(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        match = SETTING_PATTERN.fullmatch(line)
        if match is None:
            continue
        key, value = match.groups()
        previous_value = values.get(key)
        if previous_value is not None and previous_value != value:
            raise ProjectConfigError(f"参数 {key} 的值冲突")
        values[key] = value
    return values
