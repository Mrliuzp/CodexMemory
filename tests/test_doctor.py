from pathlib import Path

from codex_memory.doctor import doctor_exit_code, run_doctor


def test_doctor_reports_disabled_project(tmp_path: Path) -> None:
    report = run_doctor(tmp_path, env={}, mcp_probe=lambda: {"status": "ok"})

    assert report["project_config"] == "disabled"
    assert report["overall"] == "warning"
    assert report["project_id"] is None
    assert report["messages"]


def test_doctor_reports_enabled_project(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "CODEX_MEMORY_AUTO_LOG=required\n"
        "CODEX_MEMORY_PROJECT_ID=erp\n"
        "CODEX_MEMORY_MCP_SERVER=codex-memory\n",
        encoding="utf-8",
    )

    report = run_doctor(
        tmp_path,
        env={"CODEX_MEMORY_MCP_TOKEN": "test-token"},
        mcp_probe=lambda: {"status": "ok"},
    )

    assert report["project_config"] == "enabled"
    assert report["project_id"] == "erp"
    assert report["token_env"] == "ok"
    assert report["mcp_health"] == "ok"
    assert report["overall"] == "ok"


def test_doctor_reports_project_configuration_error(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "CODEX_MEMORY_AUTO_LOG=required\n"
        "CODEX_MEMORY_PROJECT_ID=ERP 中文\n"
        "CODEX_MEMORY_MCP_SERVER=codex-memory\n",
        encoding="utf-8",
    )

    report = run_doctor(tmp_path, env={}, mcp_probe=lambda: {"status": "ok"})

    assert report["project_config"] == "error"
    assert report["overall"] == "error"
    assert any("格式无效" in message for message in report["messages"])


def test_doctor_requires_non_placeholder_token_for_enabled_project(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "CODEX_MEMORY_AUTO_LOG=required\n"
        "CODEX_MEMORY_PROJECT_ID=erp\n"
        "CODEX_MEMORY_MCP_SERVER=codex-memory\n",
        encoding="utf-8",
    )

    report = run_doctor(tmp_path, env={"CODEX_MEMORY_MCP_TOKEN": "change-me-token"}, mcp_probe=lambda: {"status": "ok"})

    assert report["token_env"] == "missing"
    assert report["overall"] == "error"


def test_doctor_exit_codes_follow_overall_status() -> None:
    assert doctor_exit_code({"overall": "ok"}) == 0
    assert doctor_exit_code({"overall": "warning"}) == 1
    assert doctor_exit_code({"overall": "error"}) == 2

def test_doctor_reports_redacted_operations_status(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "CODEX_MEMORY_AUTO_LOG=required\n"
        "CODEX_MEMORY_PROJECT_ID=erp\n"
        "CODEX_MEMORY_MCP_SERVER=codex-memory\n",
        encoding="utf-8",
    )

    report = run_doctor(
        tmp_path,
        env={"CODEX_MEMORY_MCP_TOKEN": "mcp-secret", "CODEX_MEMORY_API_TOKEN": "api-secret"},
        mcp_probe=lambda: {"status": "ok"},
        operations_probe=lambda: {
            "status": "ok",
            "pending_jobs": 2,
            "server_outbox": 1,
            "dead_letters": 0,
            "migration_schema": "ok",
            "latest_migration": "completed",
        },
        runtime_checks=True,
    )

    assert report["operations"]["status"] == "ok"
    assert report["outbox"] == {"pending": 1, "dead_letters": 0}
    assert report["migration"] == {"ready": True, "schema": "ok", "latest": "completed"}
    assert "mcp-secret" not in str(report)
    assert "api-secret" not in str(report)


def test_doctor_fails_when_operations_probe_fails(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "CODEX_MEMORY_AUTO_LOG=required\n"
        "CODEX_MEMORY_PROJECT_ID=erp\n"
        "CODEX_MEMORY_MCP_SERVER=codex-memory\n",
        encoding="utf-8",
    )

    report = run_doctor(
        tmp_path,
        env={"CODEX_MEMORY_MCP_TOKEN": "mcp-secret"},
        mcp_probe=lambda: {"status": "ok"},
        operations_probe=lambda: {"status": "error"},
        runtime_checks=True,
    )

    assert report["operations"]["status"] == "error"
    assert report["overall"] == "error"