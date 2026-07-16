from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select


def _factory(tmp_path: Path):
    from codex_memory.db import create_session_factory

    url = f"sqlite:///{tmp_path / 'import.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_session_factory(create_engine(url))


def test_v131_migration_creates_reference_layer_tables(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    inspector = inspect(factory.kw["bind"])
    assert {"import_batches", "source_documents", "document_chunks", "reference_candidates"} <= set(inspector.get_table_names())


def test_imports_p0_formats_deduplicates_and_creates_reference_candidates(tmp_path: Path) -> None:
    from codex_memory.db_models import Base, ProjectRow
    from codex_memory.v131_import import ImportItem, KnowledgeImportService

    factory = _factory(tmp_path)
    # Alembic creates the tables; Base is included here so the ORM project model is available in SQLite metadata.
    with factory() as session:
        project = ProjectRow(project_key="demo", name="Demo")
        session.add(project)
        session.commit()

    service = KnowledgeImportService(factory)
    result = service.import_items(
        "demo",
        [
            ImportItem("README.md", "# 权限\n\n使用最小权限。\n\n## 发布\n\n发布前必须审核。", "markdown"),
            ImportItem("events.jsonl", '{"type":"error","message":"timeout"}\n{"type":"fix","message":"retry"}\n', "jsonl"),
            ImportItem("query.sql", "SELECT * FROM users;\n\nCREATE INDEX idx_users ON users(id);", "sql"),
        ],
    )
    assert result.status == "completed"
    assert result.documents == 3
    assert result.chunks >= 4
    assert result.candidates == result.chunks

    duplicate = service.import_items("demo", [ImportItem("copy.md", "# 权限\n\n使用最小权限。\n\n## 发布\n\n发布前必须审核。", "markdown")])
    assert duplicate.duplicates == 1

    hits = service.search_reference("demo", "发布 审核", limit=3)
    assert hits
    assert "审核" in hits[0]["content"]

    from codex_memory.db_models import DocumentChunkRow, ImportBatchRow, ReferenceCandidateRow, SourceDocumentRow

    with factory() as session:
        assert session.scalar(select(ImportBatchRow).where(ImportBatchRow.status == "completed")) is not None
        assert session.scalar(select(SourceDocumentRow)) is not None
        assert session.scalar(select(DocumentChunkRow)) is not None
        assert session.scalar(select(ReferenceCandidateRow)) is not None
        assert session.scalar(select(ReferenceCandidateRow).where(ReferenceCandidateRow.status == "pending_review")) is not None


def test_cli_import_reads_files_into_reference_layer(tmp_path: Path, monkeypatch, capsys) -> None:
    from codex_memory import cli
    from codex_memory.db_models import ProjectRow

    factory = _factory(tmp_path)
    with factory() as session:
        session.add(ProjectRow(project_key="demo", name="Demo"))
        session.commit()
    source = tmp_path / "guide.md"
    source.write_text("# 发布\n\n发布前必须审核。", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["codex-memory", "--db", str(tmp_path / "import.db"), "import", "--project", "demo", str(source)])
    cli.main()
    output = __import__("json").loads(capsys.readouterr().out)
    assert output["documents"] == 1


def test_invalid_jsonl_keeps_failed_import_batch(tmp_path: Path) -> None:
    from codex_memory.db_models import ImportBatchRow, ProjectRow
    from codex_memory.v131_import import ImportItem, KnowledgeImportService

    factory = _factory(tmp_path)
    with factory() as session:
        session.add(ProjectRow(project_key="demo", name="Demo"))
        session.commit()
    try:
        KnowledgeImportService(factory).import_items("demo", [ImportItem("bad.jsonl", "{bad", "jsonl")])
    except ValueError:
        pass
    else:
        raise AssertionError("无效 JSONL 应当失败")
    with factory() as session:
        failed = session.scalar(select(ImportBatchRow).where(ImportBatchRow.status == "failed"))
        assert failed is not None
