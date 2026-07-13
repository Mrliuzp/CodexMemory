from __future__ import annotations

import argparse
import json
import logging
import os

from .http_api import create_app
from .jobs import LayeringJobRunner, ReflectionJobRunner
from .mcp_server import create_server as create_mcp_server
from .models import Layer
from .service import MemoryService


def main() -> None:
    parser = argparse.ArgumentParser(prog="codex-memory")
    parser.add_argument("--db", default="memory.db")
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    serve.add_argument("--port", type=int, default=int(os.environ.get("CODEX_MEMORY_HTTP_PORT", "8765")))
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
    service = MemoryService(args.db)

    if args.command == "append":
        raw_id = service.append_conversation(
            project_id=args.project,
            conversation_id=args.conversation,
            role=args.role,
            content=args.content,
            metadata=args.metadata_json,
            process_now=args.process_now and not args.async_process,
            enqueue_async=args.enqueue_worker or args.async_process,
        )
        if args.enqueue_worker or args.async_process:
            service.drain_async_processor()
            service.stop_async_processor()
        print(json.dumps({"raw_log_id": raw_id}, ensure_ascii=False))
        return

    if args.command == "retrieve":
        results = service.retrieve(
            args.project,
            args.query,
            tags=args.tag or None,
            modules=args.module or None,
            type_tags=args.tag_type or None,
            layers=args.layer or None,
            memory_types=args.memory_type or None,
            limit=args.limit,
        )
        print(
            json.dumps(
                [
                    {
                        "id": result.item.id,
                        "project_id": result.item.project_id,
                        "layer": result.item.layer.value,
                        "title": result.item.title,
                        "memory_type": result.item.memory_type,
                        "score": result.score,
                    }
                    for result in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "context":
        if not args.skip_pending:
            service.process_project_pending_memories(args.project)
        print(
            service.build_context(
                args.project,
                args.task,
                tags=args.tag or None,
                modules=args.module or None,
                type_tags=args.tag_type or None,
                layers=args.layer or None,
                memory_types=args.memory_type or None,
                limit=args.limit,
            )
        )
        return

    if args.command == "reflect":
        print(json.dumps(service.run_reflection(args.project), ensure_ascii=False))
        return

    if args.command == "reflect-job":
        runner = ReflectionJobRunner(
            service=service,
            project_ids=args.project,
            interval_seconds=args.interval,
        )
        if args.forever:
            runner.run_forever()
            return
        print(json.dumps(runner.run_iterations(args.iterations), ensure_ascii=False, indent=2))
        return

    if args.command == "process-job":
        runner = LayeringJobRunner(
            service=service,
            interval_seconds=args.interval,
        )
        if args.forever:
            runner.run_forever()
            return
        print(json.dumps(runner.run_iterations(args.iterations), ensure_ascii=False, indent=2))
        return

    if args.command == "serve":
        import uvicorn

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        print(f"HTTP service listening at http://{args.host}:{args.port}")
        print("Request logs will stream to this console.")
        uvicorn.run(
            create_app(args.db),
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )
        return

    if args.command == "mcp":
        print("MCP server starting. Use stdin/stdout for tool calls.")
        create_mcp_server(args.db).run(transport=args.transport)
        return

    if args.command == "reports":
        print(json.dumps(service.list_reflection_reports(args.project), ensure_ascii=False, indent=2))
        return

    if args.command == "raw-logs":
        print(json.dumps(service.list_raw_logs(args.project), ensure_ascii=False, indent=2))
        return

    if args.command == "jobs":
        print(json.dumps(service.list_processing_jobs(args.project), ensure_ascii=False, indent=2))
        return

    if args.command == "promote-global":
        print(
            json.dumps(
                service.promote_to_global_l2(
                    project_id=args.project,
                    memory_id=args.memory_id,
                    reviewer=args.reviewer,
                    reason=args.reason,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "export":
        print(json.dumps(service.export_project_audit(args.project), ensure_ascii=False, indent=2))
        return

    if args.command == "rebuild":
        print(json.dumps(service.rebuild_project_from_l0(args.project), ensure_ascii=False, indent=2))
        return

    if args.command == "retry-failed":
        print(json.dumps({"retried": service.retry_failed_layering_jobs(args.project)}, ensure_ascii=False, indent=2))
        return

    if args.command == "reset-stale-running":
        print(
            json.dumps(
                {
                    "reset": service.reset_stale_running_layering_jobs(
                        args.project,
                        older_than_minutes=args.older_than_minutes,
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "health":
        print(json.dumps(service.health_status(), ensure_ascii=False, indent=2))
        return

    if args.command == "process":
        print(json.dumps({"created": service.process_pending_memories()}, ensure_ascii=False))
        return


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

