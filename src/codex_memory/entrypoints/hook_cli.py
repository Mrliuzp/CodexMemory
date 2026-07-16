"""由 Codex Hook 调用的轻量入口。"""

from __future__ import annotations

import json
import os
import sys

from .onboarding import load_config, load_token


def main() -> None:
    event_type = sys.argv[1] if len(sys.argv) > 1 else "user"
    event = json.loads(sys.stdin.read() or "{}")
    config = load_config()
    project_root = config.get("project_root") or event.get("cwd") or os.getcwd()
    values = dict(os.environ)
    values.setdefault("CODEX_MEMORY_API_URL", str(config.get("api_url", "http://127.0.0.1:8000")))
    if load_token():
        values.setdefault("CODEX_MEMORY_API_TOKEN", load_token() or "")
    values["CODEX_MEMORY_PROJECT_MAP"] = json.dumps({project_root: config.get("project_key") or project_root})
    from . import hook_runtime

    if event_type == "stop":
        hook_runtime.handle_stop(event, values)
    else:
        print(hook_runtime.handle_user_prompt(event, values), end="")


if __name__ == "__main__":
    main()
