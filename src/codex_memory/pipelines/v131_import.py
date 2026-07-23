"""V1.3.1 Knowledge Import Pipeline。

导入内容只进入 Reference Layer；正式 Memory 仍需经过既有候选策略与审核流程。
"""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
import xml.etree.ElementTree as ET
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .import_storage import ImportStorage, StoredImport, build_import_storage
from .db_models import (
    DocumentChunkRow,
    ImportBatchRow,
    ImportFileRow,
    ImportIssueRow,
    ImportUploadPartRow,
    OutboxEventRow,
    ProjectRow,
    ReferenceCandidateRow,
    SourceDocumentRow,
    MemoryRow,
    MemoryVersionRow,
    ProjectFeatureFlagRow,
)


PARSER_VERSION = "knowledge-import-v1"
SUPPORTED_TYPES = {"md", "markdown", "txt", "text", "jsonl", "json", "sql", "source", "code", "pdf", "docx", "zip"}
_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"(?i)\b(ignore|disregard|override)\s+(all\s+)?(previous|prior)\s+instructions?"),
    re.compile(r"(?i)\b(system prompt|developer message|assistant instructions?)\b"),
    re.compile(r"(?i)(忽略|无视|覆盖).{0,12}(之前|上文|系统).{0,12}(指令|提示|规则)"),
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]+?-----END [^-]+-----"),
)


def scan_security(text: str) -> tuple[list[str], str]:
    """检测提示注入和常见凭据；只对候选文本做脱敏，源文档保持可审计。"""
    issues: list[str] = []
    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            issues.append("prompt_injection")
            break
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.search(redacted):
            issues.append("sensitive_credential")
            redacted = pattern.sub("[已脱敏]", redacted)
    return sorted(set(issues)), redacted


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
    if suffix in {"pdf", "docx", "zip"}:
        return suffix
    if suffix in {"py", "js", "ts", "tsx", "jsx", "java", "go", "rs", "cs", "cpp", "h", "hpp", "yaml", "yml", "toml", "sh", "ps1"}:
        return "source"
    return "text"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolve_scope_id(session: Session, project_id: int, scope_key: str) -> int:
    """???????? knowledge_scopes ??????"""
    if not inspect(session.bind).has_table("knowledge_scopes"):
        # ????? V1.2 ???????/????????????? Scope ??
        return project_id
    row = session.execute(
        text("SELECT id FROM knowledge_scopes WHERE project_id = :project_id AND (scope_key = :scope_key OR CAST(id AS TEXT) = :scope_key OR (:scope_key IN ('project', 'default') AND is_default = :is_default))"),
        {"project_id": project_id, "scope_key": scope_key, "is_default": True},
    ).first()
    if row is None and scope_key in {"project", "default"}:
        session.execute(
            text("INSERT INTO knowledge_scopes (project_id, scope_key, name, description, is_default, status) VALUES (:project_id, 'default', :name, NULL, :is_default, 'active')"),
            {"project_id": project_id, "name": "?????", "is_default": True},
        )
        row = session.execute(
            text("SELECT id FROM knowledge_scopes WHERE project_id = :project_id AND scope_key = 'default'"),
            {"project_id": project_id},
        ).first()
    if row is None:
        raise LookupError("scope does not exist: " + scope_key)
    return int(row[0])


MAX_ZIP_FILES = 1000
MAX_ZIP_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 8 * 1024 * 1024
MAX_ZIP_DEPTH = 3


def _content_bytes(content: str) -> bytes:
    if content.startswith("base64:"):
        try:
            return base64.b64decode(content[7:], validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("二进制导入内容的 Base64 无效") from error
    return content.encode("utf-8")


def _parse_pdf_bytes(data: bytes) -> list[tuple[str | None, str, int, int]]:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
        reader = PdfReader(io.BytesIO(data))
        chunks: list[tuple[str | None, str, int, int]] = []
        for page_no, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append((f"第 {page_no} 页", text, 0, len(text)))
        if chunks:
            return chunks
    except ImportError:
        pass
    # 没有可选 PDF 库时仅提取简单字符串对象，不执行 PDF 内容流。
    values = [match.group(1).decode("latin-1", "ignore").strip() for match in re.finditer(rb"\(([^()]{2,})\)", data)]
    text = "\n".join(value for value in values if value)
    if not text:
        raise ValueError("PDF 解析器不可用或文档没有可提取文本")
    return _split_bounded(text)


def _parse_docx_bytes(data: bytes) -> list[tuple[str | None, str, int, int]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                raise ValueError("DOCX 缺少 word/document.xml")
            xml_data = archive.read("word/document.xml")
    except zipfile.BadZipFile as error:
        raise ValueError("DOCX 压缩包无效") from error
    root = ET.fromstring(xml_data)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        value = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
        if value:
            paragraphs.append(value)
    return _split_bounded("\n\n".join(paragraphs))


def _safe_zip_name(name: str) -> None:
    normalized = name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if normalized.startswith("/") or ":" in parts[0:1] or ".." in parts:
        raise ValueError(f"ZIP 成员路径不安全：{name}")


def _parse_zip_bytes(data: bytes, depth: int = 0) -> list[tuple[str | None, str, int, int]]:
    if depth > MAX_ZIP_DEPTH:
        raise ValueError("ZIP 嵌套层级超过限制")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError("ZIP 压缩包无效") from error
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise ValueError("ZIP 文件数量超过限制")
        total_size = 0
        result: list[tuple[str | None, str, int, int]] = []
        for info in infos:
            _safe_zip_name(info.filename)
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                raise ValueError(f"ZIP 成员超过大小限制：{info.filename}")
            total_size += info.file_size
            if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ValueError("ZIP 解压总大小超过限制")
            suffix = Path(info.filename).suffix.lower().lstrip(".")
            if suffix not in SUPPORTED_TYPES:
                continue
            member = archive.read(info)
            member_type = source_type_for_name(info.filename)
            if member_type == "zip":
                parts = _parse_zip_bytes(member, depth + 1)
            elif member_type == "pdf":
                parts = _parse_pdf_bytes(member)
            elif member_type == "docx":
                parts = _parse_docx_bytes(member)
            else:
                parts = parse_document(member.decode("utf-8", "replace"), member_type)
            for heading, text, start, end in parts:
                result.append((f"{info.filename}: {heading}" if heading else info.filename, text, start, end))
        if not result:
            raise ValueError("ZIP 中没有可支持的导入文件")
        return result
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
    if normalized in {"pdf", "docx", "zip"}:
        data = _content_bytes(content)
        if normalized == "pdf":
            return _parse_pdf_bytes(data)
        if normalized == "docx":
            return _parse_docx_bytes(data)
        return _parse_zip_bytes(data)
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
    def __init__(self, session_factory: sessionmaker[Session], storage: ImportStorage | None = None) -> None:
        self.session_factory = session_factory
        self.storage = storage or build_import_storage()

    def import_items(self, project_key: str, items: Iterable[ImportItem], metadata: dict[str, Any] | None = None) -> ImportResult:
        materialized = list(items)
        if not materialized:
            raise ValueError("至少需要一个导入项")
        scope_key = str((metadata or {}).get("scope_key", "project"))
        if not scope_key.strip():
            raise ValueError("scope_key 不能为空")
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"项目不存在：{project_key}")
            scope_id = _resolve_scope_id(session, project.id, scope_key)
            batch = ImportBatchRow(
                project_id=project.id,
                source_type="mixed" if len({item.source_type or source_type_for_name(item.source_name) for item in materialized}) > 1 else (materialized[0].source_type or source_type_for_name(materialized[0].source_name)),
                scope_key=scope_key,
                scope_id=scope_id,
                source_count=len(materialized),
                metadata_json=metadata or {},
                status="running",
                started_at=datetime.now(timezone.utc),
                processed_count=0,
            )
            session.add(batch)
            session.flush()
            # 先持久化批次本身，解析失败时仍保留可审计的 failed 记录。
            session.commit()
            documents = chunks = candidates = duplicates = errors = 0
            try:
                for item in materialized:
                    batch_status = session.scalar(select(ImportBatchRow.status).where(ImportBatchRow.id == batch.id))
                    if batch_status == "cancelled":
                        batch.cancelled_at = datetime.now(timezone.utc)
                        session.commit()
                        return ImportResult(batch.id, "cancelled", documents, chunks, candidates, duplicates, errors)
                    source_type = (item.source_type or source_type_for_name(item.source_name)).lower()
                    content_hash = _hash(item.content)
                    security_issues, _ = scan_security(item.content)
                    existing = session.scalar(select(SourceDocumentRow).where(SourceDocumentRow.project_id == project.id, SourceDocumentRow.content_hash == content_hash))
                    if existing is not None:
                        duplicates += 1
                        batch.processed_count += 1
                        session.commit()
                        continue
                    parts = parse_document(item.content, source_type)
                    document = SourceDocumentRow(
                        project_id=project.id,
                        scope_id=scope_id,
                        import_batch_id=batch.id,
                        source_name=item.source_name,
                        source_type=source_type,
                        content_hash=content_hash,
                        parser_version=PARSER_VERSION,
                        content=item.content,
                        metadata_json={**(item.metadata or {}), "security_issues": security_issues},
                        status="quarantined" if "prompt_injection" in security_issues else "parsed",
                    )
                    session.add(document)
                    session.flush()
                    documents += 1
                    if "prompt_injection" in security_issues:
                        errors += 1
                        document.error_message = "检测到提示注入内容，已隔离，未生成候选"
                        batch.processed_count += 1
                        session.commit()
                        continue
                    for index, (heading, chunk_content, start, end) in enumerate(parts):
                        chunk_hash = _hash(chunk_content)
                        _, safe_chunk_content = scan_security(chunk_content)
                        chunk = DocumentChunkRow(
                            project_id=project.id,
                            scope_id=scope_id,
                            document_id=document.id,
                            chunk_index=index,
                            heading=heading,
                            content=safe_chunk_content,
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
                            content={"text": safe_chunk_content, "source_name": item.source_name, "source_type": source_type, "security_issues": security_issues},
                            dedupe_key=f"{project_key}.reference.{chunk_hash}",
                            evidence_json={"document_id": document.id, "chunk_id": chunk.id, "start_char": start, "end_char": end},
                            scope_key=scope_key,
                            scope_id=scope_id,
                        )
                        session.add(candidate)
                        candidates += 1
                    batch.processed_count += 1
                    session.commit()
                batch.document_count = documents
                batch.chunk_count = chunks
                batch.error_count = errors
                batch.status = "completed"
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

    def create_batch(self, project_key: str, scope_key: str = "project", metadata: dict[str, Any] | None = None) -> int:
        """创建待上传批次；此方法不解析内容、不创建候选。"""
        if not scope_key.strip():
            raise ValueError("scope_key 不能为空")
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"项目不存在：{project_key}")
            scope_id = _resolve_scope_id(session, project.id, scope_key)
            batch = ImportBatchRow(
                project_id=project.id,
                source_type="mixed",
                scope_key=scope_key,
                scope_id=scope_id,
                source_count=0,
                status="draft",
                metadata_json={**(metadata or {}), "storage_backend": self.storage.backend},
            )
            session.add(batch)
            session.commit()
            return batch.id

    def add_files(self, batch_id: int, items: Iterable[ImportItem]) -> dict[str, Any]:
        """把原文写入不可变导入文件存储；不会在请求内解析。"""
        materialized = list(items)
        if not materialized:
            raise ValueError("至少需要一个导入文件")
        with self.session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise LookupError(f"导入批次不存在：{batch_id}")
            if batch.status not in {"draft", "uploaded", "failed", "cancelled"}:
                raise ValueError("当前批次不允许上传文件")
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise LookupError("项目不存在")
            existing_hashes = set(session.scalars(select(ImportFileRow.content_hash).where(ImportFileRow.import_batch_id == batch_id)).all())
            added = 0
            for item in materialized:
                source_type = (item.source_type or source_type_for_name(item.source_name)).lower()
                if source_type not in SUPPORTED_TYPES:
                    raise ValueError(f"不支持的导入格式：{source_type}")
                content_hash = _hash(item.content)
                if content_hash in existing_hashes:
                    continue
                stored = self.storage.put(item.content, batch.id, content_hash)
                session.add(
                    ImportFileRow(
                        project_id=project.id,
                        scope_id=batch.scope_id,
                        import_batch_id=batch.id,
                        source_name=item.source_name,
                        source_type=source_type,
                        size_bytes=len(_content_bytes(item.content)),
                        content_hash=content_hash,
                        storage_backend=stored.backend,
                        storage_key=stored.key,
                        content=stored.content,
                        metadata_json=item.metadata or {},
                        status="uploaded",
                    )
                )
                existing_hashes.add(content_hash)
                added += 1
            batch.source_count = int(batch.source_count or 0) + added
            batch.status = "uploaded"
            session.commit()
            return {"batch_id": batch_id, "added": added, "source_count": batch.source_count}

    def begin_upload(self, batch_id: int, source_name: str, source_type: str | None, total_parts: int, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """????????????????????? ImportFile?"""
        if not source_name.strip() or total_parts < 1 or total_parts > 1024:
            raise ValueError("????????")
        with self.session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise LookupError(f"????????{batch_id}")
            if batch.status not in {"draft", "uploaded", "failed", "cancelled"}:
                raise ValueError("???????????")
            source_kind = (source_type or source_type_for_name(source_name)).lower()
            if source_kind not in SUPPORTED_TYPES:
                raise ValueError(f"?????????{source_kind}")
            upload_id = uuid.uuid4().hex
            session.commit()
            return {"batch_id": batch_id, "upload_id": upload_id, "source_name": source_name, "source_type": source_kind, "total_parts": total_parts, "metadata": metadata or {}}

    def put_upload_part(self, batch_id: int, upload_id: str, part_number: int, total_parts: int, source_name: str, source_type: str | None, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """????????????? UTF-8 ? base64 ??????"""
        if part_number < 0 or total_parts < 1 or part_number >= total_parts:
            raise ValueError("??????")
        size = len(_content_bytes(content))
        if size > 4 * 1024 * 1024:
            raise ValueError("???????? 4 MiB")
        with self.session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise LookupError(f"????????{batch_id}")
            if batch.status not in {"draft", "uploaded", "failed", "cancelled"}:
                raise ValueError("???????????")
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise LookupError("?????")
            source_kind = (source_type or source_type_for_name(source_name)).lower()
            if source_kind not in SUPPORTED_TYPES:
                raise ValueError(f"?????????{source_kind}")
            existing = session.scalar(select(ImportUploadPartRow).where(ImportUploadPartRow.import_batch_id == batch_id, ImportUploadPartRow.upload_id == upload_id, ImportUploadPartRow.part_number == part_number))
            content_hash = _hash(content)
            if existing is not None:
                if existing.content_hash != content_hash or existing.total_parts != total_parts:
                    raise ValueError("?????????????")
                return {"upload_id": upload_id, "part_number": part_number, "status": existing.status, "size_bytes": existing.size_bytes}
            session.add(ImportUploadPartRow(project_id=project.id, scope_id=batch.scope_id, import_batch_id=batch.id, upload_id=upload_id, source_name=source_name, source_type=source_kind, part_number=part_number, total_parts=total_parts, size_bytes=size, content_hash=content_hash, content=content, metadata_json=metadata or {}, status="uploaded"))
            session.commit()
            return {"upload_id": upload_id, "part_number": part_number, "status": "uploaded", "size_bytes": size}

    def complete_upload(self, batch_id: int, upload_id: str) -> dict[str, Any]:
        """??????????????? ImportFile?"""
        with self.session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise LookupError(f"????????{batch_id}")
            parts = session.scalars(select(ImportUploadPartRow).where(ImportUploadPartRow.import_batch_id == batch_id, ImportUploadPartRow.upload_id == upload_id).order_by(ImportUploadPartRow.part_number)).all()
            if not parts:
                raise LookupError("?????????")
            total_parts = parts[0].total_parts
            if len(parts) != total_parts or [part.part_number for part in parts] != list(range(total_parts)):
                raise ValueError("????????")
            if any(part.total_parts != total_parts or part.status == "completed" for part in parts):
                if all(part.status == "completed" for part in parts):
                    return {"batch_id": batch_id, "upload_id": upload_id, "status": "completed", "added": 0}
                raise ValueError("???????")
            source_name = parts[0].source_name
            source_type = parts[0].source_type
            if any(part.source_name != source_name or part.source_type != source_type for part in parts):
                raise ValueError("??????????")
            if sum(int(part.size_bytes or 0) for part in parts) > 64 * 1024 * 1024:
                raise ValueError("?????????? 64 MiB")
            values = [part.content for part in parts]
            if all(value.startswith("base64:") for value in values):
                combined = "base64:" + base64.b64encode(b"".join(_content_bytes(value) for value in values)).decode("ascii")
            elif any(value.startswith("base64:") for value in values):
                raise ValueError("????????????")
            else:
                combined = "".join(values)
            metadata = parts[0].metadata_json or {}
        result = self.add_files(batch_id, [ImportItem(source_name, combined, source_type, metadata)])
        with self.session_factory() as session:
            rows = session.scalars(select(ImportUploadPartRow).where(ImportUploadPartRow.import_batch_id == batch_id, ImportUploadPartRow.upload_id == upload_id)).all()
            for row in rows:
                row.status = "completed"
            session.commit()
        return result | {"upload_id": upload_id, "status": "completed"}

    def start_batch(self, batch_id: int) -> dict[str, Any]:
        """为每个文件创建幂等 Outbox 事件，交由 Worker 异步解析。"""
        with self.session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise LookupError(f"导入批次不存在：{batch_id}")
            if batch.status not in {"draft", "uploaded", "failed", "cancelled"}:
                raise ValueError("当前批次不能启动")
            files = session.scalars(select(ImportFileRow).where(ImportFileRow.import_batch_id == batch_id).order_by(ImportFileRow.id)).all()
            if not files:
                raise ValueError("批次没有可处理文件")
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise LookupError("项目不存在")
            retry_count = int(batch.retry_count or 0)
            queued = 0
            for file in files:
                if file.status not in {"uploaded", "failed", "cancelled"}:
                    continue
                file.status = "queued"
                event_key = f"{project.project_key}.import.parse_document.file-{file.id}.{PARSER_VERSION}.retry-{retry_count}"
                existing = session.scalar(select(OutboxEventRow).where(OutboxEventRow.project_id == project.id, OutboxEventRow.idempotency_key == event_key))
                if existing is None:
                    session.add(
                        OutboxEventRow(
                            project_id=project.id,
                            aggregate_type="import_file",
                            aggregate_id=file.id,
                            event_type="document.imported.v1",
                            payload_version="v1",
                            payload={"project_id": project.id, "project_key": project.project_key, "import_batch_id": batch.id, "import_file_id": file.id, "scope_key": batch.scope_key},
                            idempotency_key=event_key,
                            priority=5,
                        )
                    )
                    queued += 1
            batch.status = "queued"
            batch.started_at = batch.started_at or datetime.now(timezone.utc)
            session.commit()
            return {"batch_id": batch_id, "status": batch.status, "queued": queued, "source_count": len(files)}

    def retry_batch(self, batch_id: int) -> dict[str, Any]:
        with self.session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise LookupError(f"导入批次不存在：{batch_id}")
            if batch.status not in {"failed", "cancelled"}:
                raise ValueError("只有失败或已取消批次可以重试")
            batch.retry_count = int(batch.retry_count or 0) + 1
            failed_files = session.scalars(select(ImportFileRow).where(ImportFileRow.import_batch_id == batch_id, ImportFileRow.status.in_(["failed", "cancelled"]))).all()
            if not failed_files:
                raise ValueError("批次没有可重试文件")
            for file in failed_files:
                file.status = "uploaded"
                file.error_message = None
            batch.error_message = None
            batch.error_count = 0
            batch.processed_count = max(0, int(batch.source_count or 0) - len(failed_files))
            batch.completed_at = None
            batch.cancelled_at = None
            batch.status = "uploaded"
            session.commit()
        return self.start_batch(batch_id)

    def process_import_file(self, import_file_id: int) -> dict[str, Any]:
        """Worker 事务：解析一个文件并生成 Reference Layer；失败隔离到文件和问题记录。"""
        with self.session_factory() as session:
            file = session.get(ImportFileRow, import_file_id)
            if file is None:
                raise LookupError(f"导入文件不存在：{import_file_id}")
            batch = session.get(ImportBatchRow, file.import_batch_id)
            project = session.get(ProjectRow, file.project_id)
            if batch is None or project is None or batch.project_id != file.project_id:
                raise ValueError("导入文件项目边界无效")
            if batch.status in {"cancelled", "rolled_back"}:
                file.status = "cancelled"
                session.commit()
                return {"file_id": file.id, "status": file.status, "candidates": 0}
            if file.status in {"parsed", "duplicate", "quarantined"}:
                return {"file_id": file.id, "status": file.status, "candidates": 0}
            file.status = "processing"
            stored = StoredImport(file.storage_backend, file.storage_key or "", file.content)
            try:
                raw_content = self.storage.get(stored)
                security_issues, _ = scan_security(raw_content)
                parts = parse_document(raw_content, file.source_type)
            except Exception as error:
                file.status = "failed"
                file.error_message = str(error)[:1000]
                batch.error_count = int(batch.error_count or 0) + 1
                batch.processed_count = int(batch.processed_count or 0) + 1
                session.add(ImportIssueRow(project_id=file.project_id, scope_id=batch.scope_id, import_batch_id=batch.id, import_file_id=file.id, issue_type="parse_error", severity="error", message=str(error)[:1000], metadata_json={"source_type": file.source_type}))
                self._finish_batch_if_done(session, batch)
                session.commit()
                return {"file_id": file.id, "status": file.status, "candidates": 0}
            existing = session.scalar(select(SourceDocumentRow).where(SourceDocumentRow.project_id == file.project_id, SourceDocumentRow.content_hash == file.content_hash))
            if existing is not None:
                file.status = "duplicate"
                batch.processed_count = int(batch.processed_count or 0) + 1
                session.add(ImportIssueRow(project_id=file.project_id, scope_id=batch.scope_id, import_batch_id=batch.id, import_file_id=file.id, source_document_id=existing.id, issue_type="duplicate", severity="info", message="内容哈希与既有源文档重复", metadata_json={"content_hash": file.content_hash}))
                self._finish_batch_if_done(session, batch)
                session.commit()
                return {"file_id": file.id, "status": file.status, "candidates": 0}
            document = SourceDocumentRow(project_id=file.project_id, scope_id=batch.scope_id, import_batch_id=batch.id, source_name=file.source_name, source_type=file.source_type, content_hash=file.content_hash, parser_version=file.parser_version, content=raw_content, metadata_json={**(file.metadata_json or {}), "security_issues": security_issues}, status="parsed")
            session.add(document)
            session.flush()
            if "prompt_injection" in security_issues:
                document.status = "quarantined"
                document.error_message = "检测到提示注入内容，已隔离，未生成候选"
                file.status = "quarantined"
                batch.error_count = int(batch.error_count or 0) + 1
                batch.processed_count = int(batch.processed_count or 0) + 1
                session.add(ImportIssueRow(project_id=file.project_id, scope_id=batch.scope_id, import_batch_id=batch.id, import_file_id=file.id, source_document_id=document.id, issue_type="prompt_injection", severity="block", message=document.error_message, metadata_json={"patterns": security_issues}))
                self._finish_batch_if_done(session, batch)
                session.commit()
                return {"file_id": file.id, "status": file.status, "candidates": 0}
            candidate_count = 0
            for index, (heading, chunk_content, start, end) in enumerate(parts):
                _, safe_content = scan_security(chunk_content)
                chunk_hash = _hash(chunk_content)
                chunk = DocumentChunkRow(project_id=file.project_id, scope_id=batch.scope_id, document_id=document.id, chunk_index=index, heading=heading, content=safe_content, content_hash=chunk_hash, start_char=start, end_char=end, metadata_json={"parser_version": file.parser_version})
                session.add(chunk)
                session.flush()
                session.add(ReferenceCandidateRow(project_id=file.project_id, scope_id=batch.scope_id, document_id=document.id, chunk_id=chunk.id, title=heading or file.source_name, content={"text": safe_content, "source_name": file.source_name, "source_type": file.source_type, "security_issues": security_issues}, dedupe_key=f"{project.project_key}.reference.{chunk_hash}", evidence_json={"document_id": document.id, "chunk_id": chunk.id, "start_char": start, "end_char": end}, scope_key=batch.scope_key))
                candidate_count += 1
            file.status = "parsed"
            batch.document_count = int(batch.document_count or 0) + 1
            batch.chunk_count = int(batch.chunk_count or 0) + len(parts)
            batch.processed_count = int(batch.processed_count or 0) + 1
            self._finish_batch_if_done(session, batch)
            session.commit()
            return {"file_id": file.id, "status": file.status, "candidates": candidate_count}

    @staticmethod
    def _finish_batch_if_done(session: Session, batch: ImportBatchRow) -> None:
        if int(batch.processed_count or 0) < int(batch.source_count or 0):
            return
        failed = int(batch.error_count or 0) > 0
        has_candidates = session.scalar(select(func.count()).select_from(ReferenceCandidateRow).join(SourceDocumentRow, SourceDocumentRow.id == ReferenceCandidateRow.document_id).where(SourceDocumentRow.import_batch_id == batch.id)) or 0
        if failed:
            batch.status = "failed"
        elif has_candidates:
            batch.status = "awaiting_review"
        else:
            batch.status = "completed"
            batch.completed_at = datetime.now(timezone.utc)
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

    def review_candidate(
        self,
        candidate_id: int,
        decision: str,
        reviewer: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """审核导入候选；批准时创建正式 Memory，拒绝时保留审计记录。"""
        if decision not in {"approve", "reject"}:
            raise ValueError("decision 必须是 approve 或 reject")
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            candidate = session.get(ReferenceCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"导入候选不存在：{candidate_id}")
            if candidate.status in {"published", "rolled_back"}:
                raise ValueError("导入候选已经完成审核，不能重复处理")
            candidate.reviewer = reviewer
            candidate.review_reason = reason
            candidate.reviewed_at = now
            if decision == "reject":
                candidate.status = "rejected"
                session.commit()
                return {"id": candidate.id, "status": candidate.status, "published_memory_id": None}
            content = candidate.content if isinstance(candidate.content, dict) else {"text": str(candidate.content)}
            memory = MemoryRow(
                project_id=candidate.project_id if candidate.scope_key != "global" else None,
                scope_id=candidate.scope_id,
                level="L2",
                memory_type="imported_reference",
                title=candidate.title,
                content=content,
                confidence=candidate.confidence,
                status="published",
                scope="global" if candidate.scope_key == "global" else "project",
                source_kind="import",
                review_status="accepted",
            )
            session.add(memory)
            session.flush()
            session.add(MemoryVersionRow(memory_id=memory.id, version=1, content=content))
            candidate.status = "published"
            candidate.published_memory_id = memory.id
            session.commit()
            return {"id": candidate.id, "status": candidate.status, "published_memory_id": memory.id}

    def rollback_candidate(self, candidate_id: int, reason: str | None = None) -> dict[str, Any]:
        """软回滚导入产生的 Memory，保留候选和源文档。"""
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            candidate = session.get(ReferenceCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"导入候选不存在：{candidate_id}")
            if candidate.published_memory_id is None:
                raise ValueError("导入候选尚未发布，不能回滚")
            memory = session.get(MemoryRow, candidate.published_memory_id)
            if memory is not None:
                memory.status = "deprecated"
                memory.deprecated = True
                memory.review_status = "rolled_back"
            candidate.status = "rolled_back"
            candidate.rolled_back_at = now
            if reason:
                candidate.review_reason = reason
            session.commit()
            return {"id": candidate.id, "status": candidate.status, "published_memory_id": candidate.published_memory_id}

    def rollback_batch(self, batch_id: int, reason: str | None = None) -> dict[str, Any]:
        """回滚批次内已发布 Memory，不删除不可变源文档和分块。"""
        with self.session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise LookupError(f"导入批次不存在：{batch_id}")
            candidate_ids = session.scalars(
                select(ReferenceCandidateRow.id)
                .join(SourceDocumentRow, SourceDocumentRow.id == ReferenceCandidateRow.document_id)
                .where(SourceDocumentRow.import_batch_id == batch_id, ReferenceCandidateRow.published_memory_id.is_not(None))
            ).all()
        rolled_back = 0
        for candidate_id in candidate_ids:
            self.rollback_candidate(int(candidate_id), reason)
            rolled_back += 1
        with self.session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is not None:
                batch.status = "rolled_back"
                batch.rolled_back_at = datetime.now(timezone.utc)
                session.commit()
        return {"batch_id": batch_id, "status": "rolled_back", "rolled_back": rolled_back}
