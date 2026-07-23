from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select


def _factory(_tmp_path: Path | None = None):
    from codex_memory.db import create_postgres_test_engine, create_session_factory

    engine = create_postgres_test_engine()
    url = engine.url.render_as_string(hide_password=False)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_session_factory(engine)

def test_v131_migration_creates_reference_layer_tables(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    inspector = inspect(factory.kw["bind"])
    assert {"import_batches", "import_files", "import_issues", "source_documents", "document_chunks", "reference_candidates"} <= set(inspector.get_table_names())
    assert {"scope_key", "scope_id", "started_at", "cancelled_at", "retry_count", "processed_count", "rolled_back_at"} <= {column["name"] for column in inspector.get_columns("import_batches")}
    assert {"scope_key", "published_memory_id", "reviewer", "review_reason", "reviewed_at", "rolled_back_at"} <= {column["name"] for column in inspector.get_columns("reference_candidates")}
    for table in ("import_files", "import_issues", "source_documents", "document_chunks", "reference_candidates"):
        assert "scope_id" in {column["name"] for column in inspector.get_columns(table)}


def test_imports_p0_formats_deduplicates_and_creates_reference_candidates(tmp_path: Path) -> None:
    from codex_memory.db_models import Base, ProjectRow
    from codex_memory.v131_import import ImportItem, KnowledgeImportService

    factory = _factory(tmp_path)
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
    monkeypatch.setenv("CODEX_MEMORY_DATABASE_URL", factory.kw["bind"].url.render_as_string(hide_password=False))
    monkeypatch.setattr("sys.argv", ["codex-memory", "import", "--project", "demo", str(source)])
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


def test_advanced_import_parsers_and_zip_path_safety() -> None:
    from codex_memory.pipelines.v131_import import parse_document
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("docs/readme.md", "# 压缩资料\n\nZIP 内容。")
    encoded = "base64:" + base64.b64encode(buffer.getvalue()).decode("ascii")
    parts = parse_document(encoded, "zip")
    assert parts and "ZIP 内容" in parts[0][1]

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../escape.txt", "禁止路径穿越")
    unsafe_encoded = "base64:" + base64.b64encode(unsafe.getvalue()).decode("ascii")
    try:
        parse_document(unsafe_encoded, "zip")
    except ValueError as error:
        assert "路径不安全" in str(error)
    else:
        raise AssertionError("应拒绝 ZIP 路径穿越")

def test_import_entities_bind_to_real_knowledge_scope_id(tmp_path: Path) -> None:
    from codex_memory.db_models import ImportBatchRow, ProjectRow, SourceDocumentRow
    from codex_memory.v131_import import ImportItem, KnowledgeImportService

    factory = _factory(tmp_path)
    with factory() as session:
        session.add(ProjectRow(project_key="demo", name="Demo"))
        session.commit()
    result = KnowledgeImportService(factory).import_items("demo", [ImportItem("scope.txt", "???????", "text")])
    with factory() as session:
        scope_id = session.execute(__import__("sqlalchemy").text("SELECT id FROM knowledge_scopes WHERE project_id = 1 AND is_default = true")).scalar_one()
        batch = session.get(ImportBatchRow, result.batch_id)
        document = session.scalar(select(SourceDocumentRow))
        assert batch.scope_id == scope_id
        assert document.scope_id == scope_id
        assert isinstance(batch.scope_id, int)
