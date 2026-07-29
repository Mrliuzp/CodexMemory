import httpx
import pytest

from codex_memory.hook_client import HookApiClient, PermanentHookError, RetryableHookError


def _payload() -> dict[str, str]:
    return {"project_key": "erp", "event_key": "e1", "role": "user", "content": "修改订单"}


def _client(handler) -> HookApiClient:
    return HookApiClient("http://memory", "secret-token", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_statuses(status: int) -> None:
    with pytest.raises(RetryableHookError):
        _client(lambda request: httpx.Response(status)).append(_payload())


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
def test_permanent_statuses_do_not_expose_token(status: int) -> None:
    with pytest.raises(PermanentHookError) as caught:
        _client(lambda request: httpx.Response(status, json={"error": "rejected"})).append(_payload())

    assert "secret-token" not in str(caught.value)


def test_connection_failure_is_retryable() -> None:
    def fail(_request):
        raise httpx.ConnectError("offline")

    with pytest.raises(RetryableHookError):
        _client(fail).append(_payload())


def test_append_and_context_return_json() -> None:
    client = _client(lambda request: httpx.Response(200, json={"path": request.url.path}))

    assert client.append(_payload()) == {"path": "/api/v1/append"}
    assert client.context("erp", "修改订单") == {"path": "/api/v1/context"}


def test_task_event_uses_v14_endpoint() -> None:
    client = _client(lambda request: httpx.Response(200, json={"path": request.url.path}))

    assert client.task_event({"project_key": "erp", "event_type": "Stop"}) == {"path": "/api/v1/task-events"}
