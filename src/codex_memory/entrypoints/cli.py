from __future__ import annotations

import argparse
import json
import logging
import os
import uuid

from .models import Layer


def main() -> None:
    parser = argparse.ArgumentParser(prog="codex-memory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="初始化项目接入配置。")
    init.add_argument("--project")
    init.add_argument("--project-root", default=os.getcwd())
    init.add_argument("--api-url", default=os.environ.get("CODEX_MEMORY_API_URL", "http://127.0.0.1:8000"))
    init.add_argument("--token")
    init.add_argument("--project-name")
    init.add_argument("--database-url", default=os.environ.get("CODEX_MEMORY_DATABASE_URL"))
    init.add_argument("--install-hook", action="store_true")

    status = subparsers.add_parser("status", help="显示项目接入状态。")
    status.add_argument("--project-root", default=os.getcwd())

    doctor = subparsers.add_parser("doctor", help="检查项目接入环境。")
    doctor.add_argument("--project-root", default=os.getcwd())
    doctor.add_argument("--cwd")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--runtime-checks", action="store_true")

    hook_install = subparsers.add_parser("hook", help="管理 Codex Hook。")
    hook_install.add_argument("action", choices=["install", "uninstall"])
    hook_install.add_argument("--project-root", default=os.getcwd())

    knowledge_import = subparsers.add_parser("import", help="导入项目资料到 Reference Layer。")
    knowledge_import.add_argument("--project", required=True)
    knowledge_import.add_argument("paths", nargs="+", help="Markdown/TXT/JSONL/SQL/源码文件路径。")

    append = subparsers.add_parser("append", help="追加一条 L0 原始对话消息。")
    append.add_argument("--project", required=True)
    append.add_argument("--conversation", required=True)
    append.add_argument("--role", required=True)
    append.add_argument("--content", required=True)
    append.add_argument("--metadata-json", type=parse_json_object)
    append.add_argument("--process-now", action="store_true")
    append.add_argument("--async-process", action="store_true")
    append.add_argument("--enqueue-worker", action="store_true")

    retrieve = subparsers.add_parser("retrieve", help="检索项目的分层记忆。")
    retrieve.add_argument("--project", required=True)
    retrieve.add_argument("--query", required=True)
    retrieve.add_argument("--tag", action="append", default=[])
    retrieve.add_argument("--module", action="append", default=[])
    retrieve.add_argument("--tag-type", action="append", default=[])
    retrieve.add_argument("--layer", action="append", type=parse_layer, default=[])
    retrieve.add_argument("--type", action="append", dest="memory_type", default=[])
    retrieve.add_argument("--limit", type=int, default=8)

    context = subparsers.add_parser("context", help="构建提示词注入上下文。")
    context.add_argument("--project", required=True)
    context.add_argument("--task", required=True)
    context.add_argument("--tag", action="append", default=[])
    context.add_argument("--module", action="append", default=[])
    context.add_argument("--tag-type", action="append", default=[])
    context.add_argument("--layer", action="append", type=parse_layer, default=[])
    context.add_argument("--type", action="append", dest="memory_type", default=[])
    context.add_argument("--limit", type=int, default=8)
    context.add_argument("--skip-pending", action="store_true")

    reflect = subparsers.add_parser("reflect", help="为项目执行离线知识反思。")
    reflect.add_argument("--project", required=True)

    serve = subparsers.add_parser("serve", help="启动 HTTP API 服务。")
    serve.add_argument("--host", default=os.environ.get("CODEX_MEMORY_HTTP_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("CODEX_MEMORY_HTTP_PORT", "8000")))
    serve.add_argument("--reload", action="store_true")

    mcp = subparsers.add_parser("mcp", help="启动 MCP 服务。")
    mcp.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")

    subparsers.add_parser("health", help="检查 API 与数据库健康状态。")

    args = parser.parse_args()

    if args.command == "doctor" and (args.cwd is not None or args.runtime_checks):
        from ..doctor import doctor_exit_code, run_doctor

        result = run_doctor(args.cwd or args.project_root, runtime_checks=args.runtime_checks)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        exit_code = doctor_exit_code(result)
        if exit_code:
            raise SystemExit(exit_code)
        return

    if args.command in {"init", "status", "doctor", "hook"}:
        from .onboarding import doctor as run_doctor, health_check, install_hooks, resolve_project_key, save_config, save_credentials, status as read_status, uninstall_hooks

        if args.command == "init":
            root = os.path.abspath(args.project_root)
            project_key = resolve_project_key(args.project, root)
            save_config({"project_key": project_key, "project_name": args.project_name or project_key, "project_root": root, "api_url": args.api_url})
            if args.token:
                save_credentials(args.token)
            hook_path = install_hooks(root) if args.install_hook else None
            project_status = "configuration_only"
            if args.database_url and args.token:
                try:
                    from .bootstrap import ensure_bootstrap
                    from .db import create_engine_from_url, create_session_factory

                    ensure_bootstrap(create_session_factory(create_engine_from_url(args.database_url)), project_key, args.token, args.project_name)
                    project_status = "project_ready"
                except Exception as error:
                    project_status = f"project_setup_failed: {error}"
            print(json.dumps({"project_key": project_key, "project_root": root, "config": str(__import__("codex_memory.onboarding", fromlist=["config_path"]).config_path()), "hook": str(hook_path) if hook_path else None, "project_status": project_status, "health": health_check(args.api_url)}, ensure_ascii=False, indent=2))
            return
        if args.command == "status":
            print(json.dumps(read_status(args.project_root), ensure_ascii=False, indent=2))
            return
        if args.command == "doctor":
            result = run_doctor(args.project_root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result["ok"]:
                raise SystemExit(1)
            return
        path = install_hooks(args.project_root) if args.action == "install" else uninstall_hooks(args.project_root)
        print(json.dumps({"action": args.action, "path": str(path)}, ensure_ascii=False))
        return

    if args.command == "import":
        from .config import Settings
        from .db import create_engine_from_url, create_session_factory
        from .v131_import import KnowledgeImportService

        database_url = Settings.from_env().database_url
        result = KnowledgeImportService(create_session_factory(create_engine_from_url(database_url))).import_paths(args.project, args.paths)
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
        return

    from .api_client import MemoryApiClient

    api_url = os.environ.get("CODEX_MEMORY_API_URL", "http://127.0.0.1:8000")
    api_token = os.environ.get("CODEX_MEMORY_API_TOKEN", "")

    if args.command == "serve":
        import uvicorn
        from .config import Settings
        from .db import create_engine_from_url, create_session_factory
        from .http_api import create_v1_app

        settings = Settings.from_env()
        app = create_v1_app(create_session_factory(create_engine_from_url(settings.database_url)))
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        uvicorn.run(app, host=args.host, port=args.port, reload=args.reload, log_level="info")
        return

    if args.command == "mcp":
        from .mcp_server import create_v1_server

        create_v1_server(MemoryApiClient(api_url, api_token)).run(transport=args.transport)
        return

    if not api_token:
        raise SystemExit("必须设置 CODEX_MEMORY_API_TOKEN 才能调用正式 API")
    client = MemoryApiClient(api_url, api_token)

    if args.command == "append":
        result = client.post(
            "/api/v1/append",
            {
                "project_key": args.project,
                "session_key": args.conversation,
                "event_key": f"cli:{args.conversation}:{uuid.uuid4()}",
                "role": args.role,
                "content": args.content,
                "source": "cli",
                "metadata": args.metadata_json or {},
            },
        )
    elif args.command == "retrieve":
        result = client.post(
            "/api/v1/search",
            {
                "project_key": args.project,
                "query": args.query,
                "layers": [layer.value for layer in args.layer],
                "memory_types": args.memory_type,
                "limit": args.limit,
            },
        )
    elif args.command == "context":
        result = client.post(
            "/api/v1/context",
            {
                "project_key": args.project,
                "task": args.task,
                "layers": [layer.value for layer in args.layer],
                "memory_types": args.memory_type,
                "limit": args.limit,
                "skip_pending": args.skip_pending,
            },
        )
    elif args.command == "reflect":
        result = client.post("/api/v1/reflect", {"project_key": args.project})
    elif args.command == "health":
        result = client.get("/api/v1/health")
    else:
        raise SystemExit(f"命令 {args.command} 尚未实现")

    print(json.dumps(result, ensure_ascii=False, indent=2))

def parse_layer(value: str) -> Layer:
    return Layer(value.upper())


def parse_json_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"JSON 对象无效：{error.msg}") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("metadata JSON 必须是对象")
    return parsed


if __name__ == '__main__':
    main()

