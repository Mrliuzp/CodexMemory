from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch) -> TestClient:
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.http_api import create_v1_app
    from codex_memory.v11_models import V11Base

    monkeypatch.setenv("CODEX_MEMORY_ADMIN_USERNAME", "memory-admin")
    monkeypatch.setenv("CODEX_MEMORY_ADMIN_PASSWORD", "correct-password")
    monkeypatch.setenv("CODEX_MEMORY_ADMIN_SESSION_SECRET", "test-session-secret")
    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    return TestClient(create_v1_app(create_session_factory(engine)))


def test_admin_login_returns_session_token_and_me(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.post(
        "/api/admin/v1/login",
        json={"username": "memory-admin", "password": "correct-password"},
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    assert token.startswith("cm1.")
    me = client.get("/api/admin/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    identity = me.json()["data"]
    assert identity["permissions"] == ["admin", "read"]
    assert identity["display_name"] == "memory-admin"
    assert identity["auth_type"] == "session"
    assert identity["expires_at"].endswith("+00:00")


def test_admin_login_allows_admin_username(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setenv("CODEX_MEMORY_ADMIN_USERNAME", "admin")

    response = client.post(
        "/api/admin/v1/login",
        json={"username": "admin", "password": "correct-password"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"].startswith("cm1.")


def test_admin_login_rejects_invalid_credentials(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.post(
        "/api/admin/v1/login",
        json={"username": "memory-admin", "password": "wrong"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("CODEX_MEMORY_ADMIN_PASSWORD", "change-me-admin-password"),
        ("CODEX_MEMORY_ADMIN_SESSION_SECRET", "change-me-session-secret"),
    ],
)
def test_admin_login_fails_closed_for_placeholder_configuration(
    monkeypatch,
    variable: str,
    value: str,
) -> None:
    client = _client(monkeypatch)
    monkeypatch.setenv(variable, value)
    password = value if variable == "CODEX_MEMORY_ADMIN_PASSWORD" else "correct-password"

    response = client.post(
        "/api/admin/v1/login",
        json={"username": "memory-admin", "password": password},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "login_not_configured"
