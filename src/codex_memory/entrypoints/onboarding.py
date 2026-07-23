"""V1.3.2 本地接入配置与 Hook 管理。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def config_path() -> Path:
    return Path(os.environ.get("CODEX_MEMORY_CONFIG_PATH", Path.home() / ".codex-memory" / "config.json"))


def credentials_path() -> Path:
    return config_path().with_name("credentials.json")


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(values: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def save_credentials(token: str) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def load_token() -> str | None:
    token = os.environ.get("CODEX_MEMORY_API_TOKEN")
    if token:
        return token
    path = credentials_path()
    if not path.exists():
        return None
    values = json.loads(path.read_text(encoding="utf-8"))
    return str(values.get("token")) if values.get("token") else None


def health_check(api_url: str) -> dict[str, Any]:
    url = api_url.rstrip("/") + "/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {"reachable": True, "status": payload.get("status", "unknown"), "response": payload}
    except (OSError, urllib.error.URLError, ValueError) as error:
        return {"reachable": False, "status": "unavailable", "error": str(error)}


def git_root(path: str | Path) -> Path | None:
    candidate = Path(path).resolve()
    try:
        output = subprocess.check_output(["git", "-C", str(candidate), "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(output.strip()).resolve()


def resolve_project_key(project: str | None, project_root: str | Path | None = None) -> str:
    if project and project.strip():
        return project.strip()
    root = git_root(project_root or Path.cwd()) or Path(project_root or Path.cwd()).resolve()
    return root.name


def _hook_entry(event: str) -> dict[str, Any]:
    return {"type": "command", "command": f"python -m codex_memory.hook_cli {event}", "timeout": 5}


def install_hooks(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    hooks_dir = root / ".codex"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    path = hooks_dir / "hooks.json"
    values: dict[str, Any] = {}
    if path.exists():
        values = json.loads(path.read_text(encoding="utf-8"))
    hooks = values.setdefault("hooks", {})
    for event in ("UserPromptSubmit", "Stop"):
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"Hook 配置格式无效：{event}")
        if not any(
            isinstance(group, dict)
            and any(isinstance(item, dict) and item.get("command") == f"python -m codex_memory.hook_cli {'user' if event == 'UserPromptSubmit' else 'stop'}" for item in group.get("hooks", []))
            for group in groups
        ):
            groups.append({"hooks": [_hook_entry("user" if event == "UserPromptSubmit" else "stop")]})
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def uninstall_hooks(project_root: str | Path) -> Path:
    path = Path(project_root).resolve() / ".codex" / "hooks.json"
    if not path.exists():
        return path
    values = json.loads(path.read_text(encoding="utf-8"))
    hooks = values.get("hooks", {})
    for event in ("UserPromptSubmit", "Stop"):
        groups = hooks.get(event, [])
        hooks[event] = [
            group
            for group in groups
            if not any(isinstance(item, dict) and str(item.get("command", "")).endswith("codex_memory.hook_cli user") or isinstance(item, dict) and str(item.get("command", "")).endswith("codex_memory.hook_cli stop") for item in group.get("hooks", []))
        ]
        if not hooks[event]:
            hooks.pop(event, None)
    if hooks:
        path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return path


def status(project_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    hook_path = root / ".codex" / "hooks.json"
    return {
        "config_path": str(config_path()),
        "credentials_present": load_token() is not None,
        "project_root": str(root),
        "project_key": load_config().get("project_key"),
        "git_root": str(git_root(root)) if git_root(root) else None,
        "hooks_installed": hook_path.exists() and "codex_memory.hook_cli" in hook_path.read_text(encoding="utf-8"),
    }


def doctor(project_root: str | Path | None = None) -> dict[str, Any]:
    values = status(project_root)
    checks = {
        "python": shutil.which("python") is not None,
        "git": values["git_root"] is not None,
        "config": config_path().exists(),
        "credentials": values["credentials_present"],
        "hooks": values["hooks_installed"],
    }
    values["checks"] = checks
    values["ok"] = all(checks.values())
    return values
