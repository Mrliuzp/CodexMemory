from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _p0_app() -> tuple[TestClient, Any]:
    """Build the real V1 app and database boundary used by the contract tests."""
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow
    from codex_memory.http_api import create_v1_app
    from codex_memory.v11_models import V11Base

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project_a = ProjectRow(project_key="project-a", name="Project A")
        project_b = ProjectRow(project_key="project-b", name="Project B")
        session.add_all([project_a, project_b])
        session.flush()
        session.add_all(
            [
                ApiKeyRow(
                    project_id=project_a.id,
                    token_hash=hashlib.sha256(b"project-a-reader").hexdigest(),
                    permissions=["read"],
                ),
                ApiKeyRow(
                    project_id=project_b.id,
                    token_hash=hashlib.sha256(b"project-b-reader").hexdigest(),
                    permissions=["read"],
                ),
            ]
        )
        session.commit()
    return TestClient(create_v1_app(factory)), factory


def _reader(token: str = "project-a-reader") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_p0_read_routes_require_bearer_authentication() -> None:
    client, _ = _p0_app()

    response = client.get("/api/admin/v1/me")

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_p0_list_contract_has_request_id_pagination_and_empty_results() -> None:
    client, _ = _p0_app()

    response = client.get(
        "/api/admin/v1/candidates",
        headers=_reader(),
        params={
            "project_key": "project-a",
            "scope_id": "scope-a",
            "page": 1,
            "page_size": 25,
            "sort": "created_at",
            "order": "desc",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == []
    assert payload["meta"] == {
        "page": 1,
        "page_size": 25,
        "total": 0,
        "has_next": False,
    }
    assert payload["request_id"]


@pytest.mark.parametrize("sort", ["content", "password", "__dict__", "created_at desc; drop table"])
def test_p0_list_rejects_sort_fields_outside_the_whitelist(sort: str) -> None:
    client, _ = _p0_app()

    response = client.get(
        "/api/admin/v1/raw-records",
        headers=_reader(),
        params={"project_key": "project-a", "scope_id": "scope-a", "sort": sort},
    )

    assert response.status_code == 422


def test_p0_list_rejects_page_sizes_over_two_hundred() -> None:
    client, _ = _p0_app()

    response = client.get(
        "/api/admin/v1/jobs",
        headers=_reader(),
        params={"project_key": "project-a", "scope_id": "scope-a", "page_size": 201},
    )

    assert response.status_code == 422


def test_p0_read_routes_do_not_expose_command_methods() -> None:
    client, _ = _p0_app()
    read_routes = [
        "/api/admin/v1/projects",
        "/api/admin/v1/scopes",
        "/api/admin/v1/raw-records",
        "/api/admin/v1/candidates",
        "/api/admin/v1/memories",
        "/api/admin/v1/jobs",
        "/api/admin/v1/outbox-events",
        "/api/admin/v1/retrieval-audits",
        "/api/admin/v1/audit-events",
    ]

    for route in read_routes:
        response = client.post(route, headers=_reader(), json={})
        assert response.status_code == 405, route

