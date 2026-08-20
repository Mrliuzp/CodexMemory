from __future__ import annotations

import pytest
from starlette.requests import Request


def _request(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def _principal(*permissions: str):
    from codex_memory.auth import Principal

    return Principal(project_key="*", permissions=frozenset(permissions))


def _endpoint(path: str, method: str):
    from codex_memory.admin.api import create_admin_router

    router = create_admin_router(lambda: None)
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"未找到路由：{method} {path}")


def test_codex_auth_routes_have_expected_methods_and_admin_boundaries(monkeypatch) -> None:
    from codex_memory.admin.api import AdminAPIError
    from codex_memory.codex_auth import CodexAuthCoordinatorClient

    client = CodexAuthCoordinatorClient()
    monkeypatch.setattr(CodexAuthCoordinatorClient, "from_env", classmethod(lambda cls: client))

    status_endpoint = _endpoint("/api/admin/v1/codex-auth/status", "GET")
    status = status_endpoint(_request("GET", "/api/admin/v1/codex-auth/status"), _principal("operations_read"))
    assert status["data"] == {
        "status": "error",
        "reason": "coordinator_not_configured",
        "message": "认证协调器未配置，请先配置协调器地址。",
    }

    for action in ("start", "cancel", "recheck"):
        endpoint = _endpoint(f"/api/admin/v1/codex-auth/{action}", "POST")
        with pytest.raises(AdminAPIError) as denied:
            endpoint(_request("POST", f"/api/admin/v1/codex-auth/{action}"), _principal("read"))
        assert denied.value.status_code == 403

        with pytest.raises(AdminAPIError) as caught:
            endpoint(_request("POST", f"/api/admin/v1/codex-auth/{action}"), _principal("admin"))
        assert caught.value.status_code == 503
        assert caught.value.code == "codex_auth_coordinator_not_configured"
        assert caught.value.message == "认证协调器未配置，请先配置协调器地址。"
        assert caught.value.meta == {"reason": "coordinator_not_configured"}
