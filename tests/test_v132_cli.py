from __future__ import annotations

import json
import subprocess
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select


def test_cli_init_status_doctor_and_hook_lifecycle(tmp_path: Path, monkeypatch, capsys) -> None:
    from codex_memory import cli

    monkeypatch.setenv("CODEX_MEMORY_CONFIG_PATH", str(tmp_path / "config" / "config.json"))
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setattr("sys.argv", ["codex-memory", "init", "--project-root", str(tmp_path), "--project", "demo", "--token", "secret", "--install-hook"])
    cli.main()
    init_output = json.loads(capsys.readouterr().out)
    assert init_output["project_key"] == "demo"
    hook_path = tmp_path / ".codex" / "hooks.json"
    assert hook_path.exists()
    assert "codex_memory.hook_cli" in hook_path.read_text(encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["codex-memory", "status", "--project-root", str(tmp_path)])
    cli.main()
    status = json.loads(capsys.readouterr().out)
    assert status["credentials_present"] is True
    assert status["hooks_installed"] is True

    monkeypatch.setattr("sys.argv", ["codex-memory", "doctor", "--project-root", str(tmp_path)])
    cli.main()
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["checks"]["config"] is True
    assert doctor["checks"]["credentials"] is True

    monkeypatch.setattr("sys.argv", ["codex-memory", "hook", "uninstall", "--project-root", str(tmp_path)])
    cli.main()
    capsys.readouterr()
    assert not hook_path.exists()


def test_cli_init_creates_project_and_credential_in_migrated_database(tmp_path: Path, monkeypatch, capsys) -> None:
    from codex_memory import cli
    from codex_memory.db import create_session_factory
    from codex_memory.db_models import ApiKeyRow, ProjectRow

    database_path = tmp_path / "memory-v1.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setenv("CODEX_MEMORY_CONFIG_PATH", str(tmp_path / "config" / "config.json"))
    monkeypatch.setattr("sys.argv", ["codex-memory", "init", "--project-root", str(tmp_path), "--project", "demo", "--token", "secret", "--database-url", database_url])
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["project_status"] == "project_ready"
    factory = create_session_factory(create_engine(database_url))
    with factory() as session:
        assert session.scalar(select(ProjectRow).where(ProjectRow.project_key == "demo")) is not None
        assert session.scalar(select(ApiKeyRow)) is not None
