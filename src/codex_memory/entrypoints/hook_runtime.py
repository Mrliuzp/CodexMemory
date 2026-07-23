"""复用仓库 Hook 实现，避免接入 CLI 依赖项目相对路径。"""

from __future__ import annotations

from pathlib import Path
import importlib.util


_path = Path(__file__).resolve().parents[3] / ".codex" / "scripts" / "hook_common.py"
_spec = importlib.util.spec_from_file_location("codex_memory_project_hook_common", _path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"找不到 Hook 实现：{_path}")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
handle_user_prompt = _module.handle_user_prompt
handle_stop = _module.handle_stop
