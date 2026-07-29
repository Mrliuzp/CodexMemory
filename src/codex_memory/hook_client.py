from __future__ import annotations

from typing import Any

import httpx


class RetryableHookError(RuntimeError):
    pass


class PermanentHookError(RuntimeError):
    pass


class HookApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
        *,
        timeout: float = 3.0,
    ) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/v1/append", payload)

    def context(self, project_id: str, task: str) -> dict[str, Any]:
        return self._post("/api/v1/context", {"project_key": project_id, "task": task})

    def task_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/v1/task-events", payload)

    send_task_event = task_event

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.post(path, json=payload)
        except httpx.RequestError as error:
            raise RetryableHookError("Codex Memory 服务暂时不可用") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableHookError(f"Codex Memory 暂时失败：HTTP {response.status_code}")
        if response.status_code >= 400:
            raise PermanentHookError(f"Codex Memory 拒绝请求：HTTP {response.status_code}")
        try:
            value = response.json()
        except ValueError as error:
            raise RetryableHookError("Codex Memory 返回了无效响应") from error
        return value if isinstance(value, dict) else {"response": value}
