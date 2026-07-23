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

    hook_install = subparsers.add_parser("hook", help="管理 Codex Hook。")
    hook_install.add_argument("action", choices=["install", "uninstall"])
    hook_install.add_argument("--project-root", default=os.getcwd())

    knowledge_import = subparsers.add_parser("import", help="导入项目资料到 Reference Layer。")
    knowledge_import.add_argument("--project", required=True)
    knowledge_import.add_argument("paths", nargs="+", help="Markdown/TXT/JSONL/SQL/源码文件路径。")

    append = subparsers.add_parser("append", help="Append one raw L0 conversation message.")
    append.add_argument("--project", required=True)
    append.add_argument("--conversation", required=True)
    append.add_argument("--role", required=True)
    append.add_argument("--content", required=True)
    append.add_argument("--metadata-json", type=parse_json_object)
    append.add_argument("--process-now", action="store_true")
    append.add_argument("--async-process", action="store_true")
    append.add_argument("--enqueue-worker", action="store_true")

    retrieve = subparsers.add_parser("retrieve", help="Retrieve layered memory for a project.")
    retrieve.add_argument("--project", required=True)
    retrieve.add_argument("--query", required=True)
    retrieve.add_argument("--tag", action="append", default=[])
    retrieve.add_argument("--module", action="append", default=[])
    retrieve.add_argument("--tag-type", action="append", default=[])
    retrieve.add_argument("--layer", action="append", type=parse_layer, default=[])
    retrieve.add_argument("--type", action="append", dest="memory_type", default=[])
    retrieve.add_argument("--limit", type=int, default=8)

    context = subparsers.add_parser("context", help="Build prompt injection context.")
    context.add_argument("--project", required=True)
    context.add_argument("--task", required=True)
    context.add_argument("--tag", action="append", default=[])
    context.add_argument("--module", action="append", default=[])
    context.add_argument("--tag-type", action="append", default=[])
    context.add_argument("--layer", action="append", type=parse_layer, default=[])
    context.add_argument("--type", action="append", dest="memory_type", default=[])
    context.add_argument("--limit", type=int, default=8)
    context.add_argument("--skip-pending", action="store_true")

    reflect = subparsers.add_parser("reflect", help="Run offline knowledge reflection for a project.")
    reflect.add_argument("--project", required=True)

    reflect_job = subparsers.add_parser("reflect-job", help="Run a schedulable reflection job.")
    reflect_job.add_argument("--project", action="append", required=True)
    reflect_job.add_argument("--interval", type=int, default=3600)
    reflect_job.add_argument("--iterations", type=int, default=1)
    reflect_job.add_argument("--forever", action="store_true")

    process_job = subparsers.add_parser("process-job", help="Run a schedulable L0 processing job.")
    process_job.add_argument("--interval", type=int, default=10)
    process_job.add_argument("--iterations", type=int, default=1)
    process_job.add_argument("--forever", action="store_true")

    serve = subparsers.add_parser("serve", help="Start the HTTP API server.")
    serve.add_argument("--host", default=os.environ.get("CODEX_MEMORY_HTTP_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("CODEX_MEMORY_HTTP_PORT", "8000")))
    serve.add_argument("--reload", action="store_true")

    mcp = subparsers.add_parser("mcp", help="Start the MCP server.")
    mcp.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")

    reports = subparsers.add_parser("reports", help="List reflection reports for a project.")
    reports.add_argument("--project", required=True)

    raw_logs = subparsers.add_parser("raw-logs", help="List raw logs for a project.")
    raw_logs.add_argument("--project", required=True)

    jobs = subparsers.add_parser("jobs", help="List processing jobs for a project.")
    jobs.add_argument("--project", required=True)

    promote = subparsers.add_parser("promote-global", help="Promote a project L2 memory to global L2.")
    promote.add_argument("--project", required=True)
    promote.add_argument("--memory-id", type=int, required=True)
    promote.add_argument("--reviewer", required=True)
    promote.add_argument("--reason", required=True)

    export = subparsers.add_parser("export", help="Export project audit data.")
    export.add_argument("--project", required=True)

    rebuild = subparsers.add_parser("rebuild", help="Rebuild project derived memories from L0.")
    rebuild.add_argument("--project", required=True)

    retry_failed = subparsers.add_parser("retry-failed", help="重试失败的 L0 处理任务。")
    retry_failed.add_argument("--project", required=True)

    reset_stale = subparsers.add_parser("reset-stale-running", help="Reset stale running L0 jobs.")
    reset_stale.add_argument("--project", required=True)
    reset_stale.add_argument("--older-than-minutes", type=int, default=30)

    subparsers.add_parser("health", help="Check database health.")
    subparsers.add_parser("process", help="Process all pending L0 jobs.")

    args = parser.parse_args()

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
        raise SystemExit(f"命令 {args.command} 属于已删除的旧本地运行时，请使用正式 API 或管理后台")

    print(json.dumps(result, ensure_ascii=False, indent=2))

def parse_layer(value: str) -> Layer:
    return Layer(value.upper())


def parse_json_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"JSON 对象无效：{error.msg}") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("metadata JSON 必须是对象（must be an object）")
    return parsed


if __name__ == '__main__':
    main()

