from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import is_placeholder_value
from .project_config import ProjectConfigError, load_project_memory_config


MCP_URL = "http://127.0.0.1:8001/mcp"


def doctor_exit_code(report: Mapping[str, Any]) -> int:
    return {"ok": 0, "warning": 1, "error": 2}.get(str(report.get("overall")), 2)


def run_doctor(
    cwd: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    mcp_probe: Callable[[], Mapping[str, Any]] | None = None,
    runtime_checks: bool = False,
) -> dict[str, Any]:
    values = dict(os.environ if env is None else env)
    report: dict[str, Any] = {
        "codex_cli": "not_checked",
        "mcp_registration": "not_checked",
        "mcp_health": "not_checked",
        "skill": "not_checked",
        "project_config": "disabled",
        "project_id": None,
        "token_env": "missing",
        "overall": "warning",
        "messages": [],
    }

    try:
        project = load_project_memory_config(cwd)
    except ProjectConfigError as error:
        report["project_config"] = "error"
        report["overall"] = "error"
        report["messages"].append(str(error))
        return report

    if not project.enabled:
        report["messages"].append("当前项目未启用 Codex Memory 自动归档。")
        return report

    report["project_config"] = "enabled"
    report["project_id"] = project.project_id
    token = values.get("CODEX_MEMORY_MCP_TOKEN")
    if not is_placeholder_value(token):
        report["token_env"] = "ok"
    else:
        report["messages"].append("CODEX_MEMORY_MCP_TOKEN 缺失或仍为占位符。")

    probe = mcp_probe or (lambda: _probe_mcp(values))
    try:
        report["mcp_health"] = "ok" if probe().get("status") == "ok" else "error"
    except Exception:
        report["mcp_health"] = "error"
    if report["mcp_health"] != "ok":
        report["messages"].append("Codex Memory MCP 服务不可用。")

    if runtime_checks:
        report["codex_cli"] = _probe_codex_cli(values)
        report["mcp_registration"] = _probe_mcp_registration(
            values,
            project.mcp_server or "codex-memory",
            report["codex_cli"],
        )
        report["skill"] = _probe_skill(values)
        _append_runtime_messages(report)

    if report["token_env"] != "ok" or report["mcp_health"] != "ok":
        report["overall"] = "error"
    else:
        report["overall"] = "ok"
        report["messages"].append("Codex Memory 项目配置与 MCP 服务均可用。")
    return report


def _probe_mcp(env: Mapping[str, str]) -> dict[str, str]:
    token = env.get("CODEX_MEMORY_MCP_TOKEN")
    if is_placeholder_value(token):
        return {"status": "error"}
    request = urllib.request.Request(
        env.get("CODEX_MEMORY_MCP_URL", MCP_URL),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return {"status": "ok" if response.status < 500 else "error"}
    except urllib.error.HTTPError as error:
        return {"status": "ok" if error.code in {405, 406} else "error"}
    except OSError:
        return {"status": "error"}


def _codex_cli_path(env: Mapping[str, str]) -> str | None:
    configured = env.get("CODEX_MEMORY_CODEX_CLI")
    if configured and Path(configured).is_file():
        return configured
    app_data = env.get("APPDATA")
    if app_data:
        candidate = Path(app_data) / "npm" / "codex.cmd"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("codex.cmd") or shutil.which("codex")


def _probe_codex_cli(env: Mapping[str, str]) -> str:
    return "ok" if _codex_cli_path(env) else "missing"


def _probe_mcp_registration(env: Mapping[str, str], server: str, cli_status: str) -> str:
    if cli_status != "ok":
        return "missing"
    cli_path = _codex_cli_path(env)
    if cli_path is None:
        return "missing"
    try:
        result = subprocess.run(
            [cli_path, "mcp", "get", server, "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError:
        return "error"
    return "ok" if result.returncode == 0 else "missing"


def _probe_skill(env: Mapping[str, str]) -> str:
    codex_home = Path(env.get("CODEX_HOME", Path.home() / ".codex"))
    return "ok" if (codex_home / "skills" / "codex-memory-auto-log" / "SKILL.md").is_file() else "missing"


def _append_runtime_messages(report: dict[str, Any]) -> None:
    if report["codex_cli"] != "ok":
        report["messages"].append("未找到 Codex CLI。")
    if report["mcp_registration"] != "ok":
        report["messages"].append("未找到有效的 Codex Memory MCP 注册。")
    if report["skill"] != "ok":
        report["messages"].append("未找到 Codex Memory 自动归档 Skill。")