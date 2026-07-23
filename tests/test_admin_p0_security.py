from __future__ import annotations

import hashlib
from typing import Any

from fastapi.testclient import TestClient


def _security_app() -> tuple[TestClient, Any, int]:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow, MessageRow, ProjectRow
    from codex_memory.http_api import create_v1_app
    from codex_memory.v11_models import MemoryCandidateRow, V11Base

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project_a = ProjectRow(project_key="project-a", name="Project A")
        project_b = ProjectRow(project_key="project-b", name="Project B")
        session.add_all([project_a, project_b])
        session.flush()
        session.add(
            ApiKeyRow(
                project_id=project_a.id,
                token_hash=hashlib.sha256(b"project-a-reader").hexdigest(),
                permissions=["read"],
            )
        )
        source = MessageRow(
            project_id=project_a.id,
            session_id=1,
            event_key="event-1",
            role="user",
            content="source content containing a bearer-secret",
            content_hash=hashlib.sha256(b"source content containing a bearer-secret").hexdigest(),
            metadata_json={"api_key": "bearer-secret", "authorization": "Bearer bearer-secret"},
        )
        session.add(source)
        session.flush()
        candidate = MemoryCandidateRow(
            project_id=project_a.id,
            source_message_id=source.id,
            task_type="error_memory",
            level="L2",
            scope="project",
            memory_type="rule",
            title="redacted candidate",
            content={
                "text": "safe summary",
                "raw": "source content containing a bearer-secret",
                "api_key": "bearer-secret",
            },
            status="generated",
        )
        session.add(candidate)
        session.commit()
        candidate_id = candidate.id
    return TestClient(create_v1_app(factory)), factory, candidate_id


def _reader() -> dict[str, str]:
    return {"Authorization": "Bearer project-a-reader"}


def test_p0_cross_project_access_is_forbidden_even_with_a_valid_token() -> None:
    client, _, _ = _security_app()

    response = client.get(
        "/api/admin/v1/candidates",
        headers=_reader(),
        params={"project_key": "project-b", "scope_id": "scope-b"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "project_access_denied"


def test_p0_cross_scope_access_is_forbidden_within_a_project() -> None:
    client, _, _ = _security_app()

    response = client.get(
        "/api/admin/v1/candidates",
        headers=_reader(),
        params={"project_key": "project-a", "scope_id": "scope-not-granted"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "scope_access_denied"


def test_p0_candidate_response_is_redacted_and_never_returns_credentials_or_raw_content() -> None:
    client, _, _ = _security_app()

    response = client.get(
        "/api/admin/v1/candidates",
        headers=_reader(),
        params={"project_key": "project-a", "scope_id": "scope-a"},
    )

    assert response.status_code == 200
    body = response.text
    assert "bearer-secret" not in body
    assert "authorization" not in body.lower()
    assert "api_key" not in body.lower()
    assert response.json()["data"][0]["content"] == {"text": "safe summary"}


def test_p0_read_only_candidate_route_cannot_be_used_to_publish_or_review() -> None:
    client, _, candidate_id = _security_app()

    for suffix in (f"/{candidate_id}:publish", f"/{candidate_id}/review"):
        response = client.post(
            f"/api/admin/v1/candidates{suffix}",
            headers=_reader(),
            json={"decision": "approve"},
        )
        assert response.status_code in {404, 405}, suffix

