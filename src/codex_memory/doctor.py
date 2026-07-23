from __future__ import annotations

import json
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
ADMIN_URL = "http://127.0.0.1:5174"


def doctor_exit_code(report: Mapping[str, Any]) -> int:
    return {"ok": 0, "warning": 1, "error": 2}.get(str(report.get("overall")), 2)


def run_doctor(
    cwd: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    mcp_probe: Callable[[], Mapping[str, Any]] | None = None,
    operations_probe: Callable[[], Mapping[str, Any]] | None = None,
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
        "operations": {"status": "not_checked"},
        "outbox": {"pending": None, "dead_letters": None},
        "migration": {"ready": False, "schema": "unknown", "latest": None},
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
    if not is_placeholder_value(values.get("CODEX_MEMORY_MCP_TOKEN")):
        report["token_env"] = "ok"
    else:
        report["messages"].append("CODEX_MEMORY_MCP_TOKEN 缺失或仍为占位符。")

    probe_mcp = mcp_probe or (lambda: _probe_mcp(values))
    try:
        report["mcp_health"] = "ok" if probe_mcp().get("status") == "ok" else "error"
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
        probe_operations = operations_probe or (lambda: _probe_operations(values))
        try:
            operations = dict(probe_operations())
        except Exception:
            operations = {"status": "error"}
        report["operations"] = operations
        if operations.get("status") == "ok":
            report["outbox"] = {
                "pending": operations.get("server_outbox", 0),
                "dead_letters": operations.get("dead_letters", 0),
            }
            report["migration"] = {
                "ready": operations.get("migration_schema") == "ok",
                "schema": operations.get("migration_schema", "unknown"),
                "latest": operations.get("latest_migration"),
            }
        _append_runtime_messages(report)

    if (
        report["token_env"] != "ok"
        or report["mcp_health"] != "ok"
        or (runtime_checks and report["operations"].get("status") != "ok")
    ):
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


def _probe_operations(env: Mapping[str, str]) -> dict[str, Any]:
    token = env.get("CODEX_MEMORY_API_TOKEN") or env.get("CODEX_MEMORY_SERVICE_TOKEN")
    if is_placeholder_value(token):
        return {"status": "missing"}
    base_url = env.get("CODEX_MEMORY_ADMIN_URL", ADMIN_URL).rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/api/admin/v1/system/status",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.load(response)
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError):
        return {"status": "error"}
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {"status": "error"}
    return {
        "status": "ok",
        "pending_jobs": data.get("pending_jobs", 0),
        "server_outbox": data.get("server_outbox", 0),
        "dead_letters": data.get("dead_letters", 0),
        "migration_schema": data.get("migration_schema", "unknown"),
        "latest_migration": data.get("latest_migration"),
    }


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
    if report["operations"].get("status") != "ok":
        report["messages"].append("运行状态服务不可用。")