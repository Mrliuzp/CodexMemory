from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from codex_memory.http_api import create_app


def test_http_health_reports_database_status(tmp_path):
    app = create_app(tmp_path / "memory.db")

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["missing_tables"] == []


def test_http_append_retrieve_and_context_share_the_same_database(tmp_path):
    app = create_app(tmp_path / "memory.db")

    with TestClient(app) as client:
        append_response = client.post(
            "/append",
            json={
                "project": "project-a",
                "conversation": "conv-1",
                "role": "user",
                "content": "Bug: http api append should persist raw logs",
                "process_now": True,
            },
        )
        retrieve_response = client.post(
            "/retrieve",
            json={
                "project": "project-a",
                "query": "http api append",
            },
        )
        context_response = client.post(
            "/context",
            json={
                "project": "project-a",
                "task": "Fix http api append bug",
            },
        )

    assert append_response.status_code == 200
    assert append_response.json()["raw_log_id"] > 0

    assert retrieve_response.status_code == 200
    results = retrieve_response.json()["results"]
    assert results
    assert results[0]["project_id"] == "project-a"

    assert context_response.status_code == 200
    context = context_response.json()["context"]
    assert "Fix http api append bug" in context
    assert "http api append should persist raw logs" in context


def test_http_requests_are_logged_to_console(tmp_path, caplog):
    app = create_app(tmp_path / "memory.db")

    caplog.set_level(logging.INFO, logger="codex_memory.http")

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert any("GET /health" in record.message for record in caplog.records)
    assert any("-> 200" in record.message for record in caplog.records)
