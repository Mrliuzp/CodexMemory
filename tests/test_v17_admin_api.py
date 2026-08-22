"""V1.7 管理接口契约测试。

本文件不连接真实数据库，也不执行模型调用；运行时数据库和 Codex CLI 由集成环境提供。
测试通过路由元数据和源码契约确保项目隔离、管理员门禁以及核心服务编排不会回退为直接写库。
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "src" / "codex_memory" / "admin" / "api.py"


def _source() -> str:
    return API.read_text(encoding="utf-8")


def _route_paths() -> set[tuple[str, str]]:
    tree = ast.parse(_source(), filename=str(API))
    paths: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr not in {"get", "put", "post"} or not decorator.args:
                continue
            path = decorator.args[0]
            if isinstance(path, ast.Constant) and isinstance(path.value, str):
                paths.add((decorator.func.attr.upper(), path.value))
    return paths


def test_v17_admin_routes_are_registered_under_actual_admin_prefix() -> None:
    routes = _route_paths()
    expected = {
        ("GET", "/projects/{project_key}/memory-window-policy"),
        ("PUT", "/projects/{project_key}/memory-window-policy"),
        ("GET", "/projects/{project_key}/memory-windows"),
        ("GET", "/projects/{project_key}/memory-windows/{window_id}"),
        ("POST", "/projects/{project_key}/memory-windows/{session_id}/seal"),
        ("GET", "/projects/{project_key}/memory-change-sets"),
        ("GET", "/projects/{project_key}/memory-change-sets/{change_set_id}"),
        ("POST", "/projects/{project_key}/memory-change-sets/{change_set_id}/approve"),
        ("POST", "/projects/{project_key}/memory-change-sets/{change_set_id}/reject"),
        ("POST", "/projects/{project_key}/memory-change-sets/{change_set_id}/apply"),
    }
    assert expected <= routes
    assert all(path.startswith("/projects/") for _, path in expected)


def test_v17_endpoints_use_admin_gate_and_project_context() -> None:
    source = _source()
    # 每个 V1.7 端点都通过公共管理员门禁；项目上下文使用 strict_access 防止跨项目访问。
    assert source.count("_require_v17_admin(request, current)") >= 9
    assert source.count('access_error_code="permission_denied", strict_access=True') >= 10


def test_v17_writes_are_delegated_to_core_services() -> None:
    source = _source()
    assert "v17_changes.seal_and_generate(" in source
    assert "v17_changes.approve(" in source
    assert "v17_changes.reject(" in source
    assert "CandidatePolicyService(session_factory).apply_window_change_set(" in source
    # 管理 API 不应出现 MemoryRow/ChangeSetRow 的 add、delete 或 commit。
    assert "session.add(MemoryRow" not in source
    assert "session.add(MemoryChangeSetRow" not in source
    v17_source = source[source.index("# V1.7 管理接口"):source.index('@router.post("/contract-services")')]
    assert "session.commit()" not in v17_source


def test_v17_review_and_apply_have_explicit_idempotency_paths() -> None:
    source = _source()
    assert 'if row.status == desired:' in source
    assert '"idempotent": True' in source
    assert 'if row.status == "applied":' in source
    assert '"idempotent": False' in source
