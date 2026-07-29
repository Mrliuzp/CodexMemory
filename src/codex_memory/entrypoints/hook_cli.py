"""由 Codex Hook 调用的轻量入口。"""

from __future__ import annotations

import json
import os
import sys

from .onboarding import load_config, load_token


def main() -> None:
    event_type = sys.argv[1] if len(sys.argv) > 1 else "user"
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if not isinstance(event, dict):
            return
        config = load_config()
        project_root = config.get("project_root") or event.get("cwd") or os.getcwd()
        values = dict(os.environ)
        values.setdefault("CODEX_MEMORY_API_URL", str(config.get("api_url", "http://127.0.0.1:8001")))
        token = load_token()
        if token:
            values.setdefault("CODEX_MEMORY_API_TOKEN", token)
        values["CODEX_MEMORY_PROJECT_MAP"] = json.dumps({project_root: config.get("project_key") or project_root})
        from . import hook_runtime

        result = hook_runtime.handle_event(event_type, event, values)
        if isinstance(result, str):
            print(result, end="")
    except (OSError, ValueError, TypeError, KeyError):
        # Hook 失败不得阻断 Codex 主流程。
        return


if __name__ == "__main__":
    main()
