"""V1.3.1 Knowledge Import Pipeline。

导入内容只进入 Reference Layer；正式 Memory 仍需经过既有候选策略与审核流程。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    DocumentChunkRow,
    ImportBatchRow,
    ProjectRow,
    ReferenceCandidateRow,
    SourceDocumentRow,
)


PARSER_VERSION = "knowledge-import-v1"
SUPPORTED_TYPES = {"md", "markdown", "txt", "text", "jsonl", "json", "sql", "source", "code"}


@dataclass(frozen=True)
class ImportItem:
    source_name: str
    content: str
    source_type: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ImportResult:
    batch_id: int
    status: str
    documents: int
    chunks: int
    candidates: int
    duplicates: int
    errors: int


def source_type_for_name(name: str) -> str:
    suffix = Path(name).suffix.lower().lstrip(".")
    if suffix in {"md", "markdown"}:
        return "markdown"
    if suffix in {"txt", "text"}:
        return "text"
    if suffix in {"jsonl", "ndjson"}:
        return "jsonl"
    if suffix == "json":
        return "json"
    if suffix == "sql":
        return "sql"
    if suffix in {"py", "js", "ts", "tsx", "jsx", "java", "go", "rs", "cs", "cpp", "h", "hpp", "yaml", "yml", "toml", "sh", "ps1"}:
        return "source"
    return "text"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _split_bounded(text: str, max_chars: int = 1600) -> list[tuple[str | None, str, int, int]]:
    result: list[tuple[str | None, str, int, int]] = []
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text):
        value = match.group(0).strip()
        if not value:
            continue
        start = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
        if len(value) <= max_chars:
            result.append((None, value, start, start + len(value)))
            continue
        for offset in range(0, len(value), max_chars):
            piece = value[offset : offset + max_chars].strip()
            if piece:
                piece_start = start + offset
                result.append((None, piece, piece_start, piece_start + len(piece)))
    if not result and text.strip():
        value = text.strip()
        for offset in range(0, len(value), max_chars):
            piece = value[offset : offset + max_chars].strip()
            if piece:
                result.append((None, piece, offset, offset + len(piece)))
    return result


def parse_document(content: str, source_type: str) -> list[tuple[str | None, str, int, int]]:
    """解析 P0 文本格式；不会执行 SQL，也不会调用外部模型。"""
    normalized = source_type.lower()
    if normalized not in SUPPORTED_TYPES:
        raise ValueError(f"不支持的导入格式：{source_type}")
    if normalized in {"jsonl", "json"}:
        chunks: list[tuple[str | None, str, int, int]] = []
        cursor = 0
        for line in content.splitlines(keepends=True):
            raw = line.rstrip("\r\n")
            if raw.strip():
                try:
                    json.loads(raw)
                except json.JSONDecodeError as error:
                    raise ValueError(f"JSONL 第 {len(chunks) + 1} 行无效：{error.msg}") from error
                start = cursor + len(raw) - len(raw.lstrip())
                chunks.append((None, raw.strip(), start, start + len(raw.strip())))
            cursor += len(line)
        return chunks
    if normalized in {"markdown", "md"}:
        headings = list(re.finditer(r"(?m)^\s{0,3}(#{1,6})\s+(.+?)\s*$", content))
        if not headings:
            return _split_bounded(content)
        result: list[tuple[str | None, str, int, int]] = []
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
            section = content[heading.start() : end].strip()
            if section:
                section_start = heading.start() + len(content[heading.start() : end]) - len(content[heading.start() : end].lstrip())
                if len(section) <= 1600:
                    result.append((heading.group(2).strip(), section, section_start, section_start + len(section)))
                else:
                    result.extend((heading.group(2).strip(), piece, section_start + local_start, section_start + local_end) for _, piece, local_start, local_end in _split_bounded(section))
        return result or _split_bounded(content)
    return _split_bounded(content)


class KnowledgeImportService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def import_items(self, project_key: str, items: Iterable[ImportItem], metadata: dict[str, Any] | None = None) -> ImportResult:
        materialized = list(items)
        if not materialized:
            raise ValueError("至少需要一个导入项")
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"项目不存在：{project_key}")
            batch = ImportBatchRow(
                project_id=project.id,
                source_type="mixed" if len({item.source_type or source_type_for_name(item.source_name) for item in materialized}) > 1 else (materialized[0].source_type or source_type_for_name(materialized[0].source_name)),
                source_count=len(materialized),
                metadata_json=metadata or {},
                status="running",
            )
            session.add(batch)
            session.flush()
            # 先持久化批次本身，解析失败时仍保留可审计的 failed 记录。
            session.commit()
            documents = chunks = candidates = duplicates = errors = 0
            try:
                for item in materialized:
                    source_type = (item.source_type or source_type_for_name(item.source_name)).lower()
                    content_hash = _hash(item.content)
                    existing = session.scalar(select(SourceDocumentRow).where(SourceDocumentRow.project_id == project.id, SourceDocumentRow.content_hash == content_hash))
                    if existing is not None:
                        duplicates += 1
                        continue
                    parts = parse_document(item.content, source_type)
                    document = SourceDocumentRow(
                        project_id=project.id,
                        import_batch_id=batch.id,
                        source_name=item.source_name,
                        source_type=source_type,
                        content_hash=content_hash,
                        parser_version=PARSER_VERSION,
                        content=item.content,
                        metadata_json=item.metadata or {},
                        status="parsed",
                    )
                    session.add(document)
                    session.flush()
                    documents += 1
                    for index, (heading, chunk_content, start, end) in enumerate(parts):
                        chunk_hash = _hash(chunk_content)
                        chunk = DocumentChunkRow(
                            project_id=project.id,
                            document_id=document.id,
                            chunk_index=index,
                            heading=heading,
                            content=chunk_content,
                            content_hash=chunk_hash,
                            start_char=start,
                            end_char=end,
                            metadata_json={"parser_version": PARSER_VERSION},
                        )
                        session.add(chunk)
                        session.flush()
                        chunks += 1
                        candidate = ReferenceCandidateRow(
                            project_id=project.id,
                            document_id=document.id,
                            chunk_id=chunk.id,
                            title=heading or item.source_name,
                            content={"text": chunk_content, "source_name": item.source_name, "source_type": source_type},
                            dedupe_key=f"{project_key}.reference.{chunk_hash}",
                            evidence_json={"document_id": document.id, "chunk_id": chunk.id, "start_char": start, "end_char": end},
                        )
                        session.add(candidate)
                        candidates += 1
                batch.document_count = documents
                batch.chunk_count = chunks
                batch.error_count = errors
                batch.status = "completed"
                from datetime import datetime, timezone
                batch.completed_at = datetime.now(timezone.utc)
                session.commit()
            except Exception as error:
                session.rollback()
                with self.session_factory() as failed_session:
                    failed = failed_session.get(ImportBatchRow, batch.id)
                    if failed is not None:
                        failed.status = "failed"
                        failed.error_count = errors + 1
                        failed.error_message = str(error)[:1000]
                        failed_session.commit()
                raise
            return ImportResult(batch.id, batch.status, documents, chunks, candidates, duplicates, errors)

    def import_paths(self, project_key: str, paths: Iterable[str | Path]) -> ImportResult:
        items: list[ImportItem] = []
        for path_value in paths:
            path = Path(path_value)
            if not path.is_file():
                raise FileNotFoundError(str(path))
            items.append(ImportItem(path.as_posix(), path.read_text(encoding="utf-8"), source_type_for_name(path.name)))
        return self.import_items(project_key, items)

    def search_reference(self, project_key: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        terms = {term.lower() for term in re.findall(r"[\w-]+", query) if len(term) > 1}
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"项目不存在：{project_key}")
            rows = session.scalars(select(DocumentChunkRow).where(DocumentChunkRow.project_id == project.id)).all()
            scored = []
            for row in rows:
                tokens = set(re.findall(r"[\w-]+", row.content.lower()))
                content_lower = row.content.lower()
                matched = {term for term in terms if term in tokens or term in content_lower}
                score = len(matched) / max(len(terms), 1)
                if score > 0:
                    scored.append((score, row))
            scored.sort(key=lambda pair: (-pair[0], pair[1].id))
            return [{"chunk_id": row.id, "score": score, "content": row.content, "heading": row.heading} for score, row in scored[:limit]]
