"""V1.5 Contract Service、Revision 与发布事务服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .api_operations import OpenAPIContractError, NormalizedOpenAPI, parse_and_normalize_openapi
from .persistence.v15_models import ApiOperationRow, ContractRevisionRow, ContractServiceRow


class ContractRevisionConflictError(ValueError):
    """表示 Revision 状态、哈希或操作身份冲突。"""

    def __init__(self, message: str, code: str = "revision_conflict", meta: dict[str, Any] | None = None) -> None:
        self.code = code
        self.meta = meta or {}
        super().__init__(message)


class ContractRevisionService:
    """同步完成 OpenAPI 解析、持久化和发布的服务。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_service(self, project_id: int, service_key: str, name: str | None = None, description: str | None = None) -> ContractServiceRow:
        service_key = service_key.strip()
        name = (name or service_key).strip()
        if not service_key or not name:
            raise ValueError("服务标识和名称不能为空")
        with self.session_factory() as session:
            row = ContractServiceRow(project_id=project_id, service_key=service_key, name=name, description=description)
            session.add(row)
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise ContractRevisionConflictError("项目内服务标识已存在", "service_exists") from error
            session.refresh(row)
            return row

    def list_services(self, project_id: int | None = None, status: str | None = None, keyword: str | None = None) -> list[ContractServiceRow]:
        with self.session_factory() as session:
            query = select(ContractServiceRow).distinct()
            if project_id is not None:
                query = query.where(ContractServiceRow.project_id == project_id)
            if status or keyword:
                query = query.outerjoin(ContractRevisionRow, ContractRevisionRow.service_id == ContractServiceRow.id)
            if status:
                query = query.where(ContractRevisionRow.status == status)
            if keyword:
                pattern = f"%{keyword.strip()}%"
                query = query.where(ContractServiceRow.service_key.ilike(pattern) | ContractServiceRow.name.ilike(pattern))
            rows = session.scalars(query.order_by(ContractServiceRow.created_at, ContractServiceRow.id)).all()
            for row in rows:
                session.expunge(row)
            return rows

    def get_service(self, service_id: int, project_id: int | None = None) -> ContractServiceRow:
        with self.session_factory() as session:
            query = select(ContractServiceRow).where(ContractServiceRow.id == service_id)
            if project_id is not None:
                query = query.where(ContractServiceRow.project_id == project_id)
            row = session.scalar(query)
            if row is None:
                raise LookupError("服务不存在")
            session.expunge(row)
            return row

    def service_detail(self, service_id: int, project_id: int | None = None) -> dict[str, Any]:
        service = self.get_service(service_id, project_id)
        with self.session_factory() as session:
            revisions = session.scalars(select(ContractRevisionRow).where(ContractRevisionRow.service_id == service.id).order_by(ContractRevisionRow.revision_number)).all()
            summaries = []
            for row in revisions:
                summary = _revision_summary(row)
                summary["operation_count"] = int(session.scalar(select(func.count(ApiOperationRow.id)).where(ApiOperationRow.revision_id == row.id)) or 0)
                summaries.append(summary)
            return {"service": _service_dict(service), "revisions": summaries}

    def create_revision(self, service_id: int, filename: str, content: bytes, project_id: int | None = None, created_by: str = "system") -> tuple[ContractRevisionRow, bool]:
        # 先完成 CPU 侧解析，数据库事务只负责锁服务、编号和写入。
        parsed = parse_and_normalize_openapi(filename, content)
        for _attempt in range(3):
            try:
                with self.session_factory() as session:
                    with session.begin():
                        service_query = select(ContractServiceRow).where(ContractServiceRow.id == service_id).with_for_update()
                        if project_id is not None:
                            service_query = service_query.where(ContractServiceRow.project_id == project_id)
                        service = session.scalar(service_query)
                        if service is None:
                            raise LookupError("服务不存在")
                        existing = session.scalar(select(ContractRevisionRow).where(ContractRevisionRow.service_id == service.id, ContractRevisionRow.content_hash == parsed.content_hash))
                        if existing is not None:
                            session.expunge(existing)
                            return existing, True
                        previous_revision = None
                        if service.current_published_revision_id is not None:
                            previous_revision = session.get(ContractRevisionRow, service.current_published_revision_id)
                        if previous_revision is None:
                            previous_revision = session.scalar(select(ContractRevisionRow).where(ContractRevisionRow.service_id == service.id, ContractRevisionRow.status == "published").order_by(ContractRevisionRow.revision_number.desc()))
                        previous_operations: list[dict[str, Any]] = []
                        if previous_revision is not None:
                            previous_operations = [
                                {"method": row.method, "path": row.path, "operation_id": row.operation_id}
                                for row in session.scalars(select(ApiOperationRow).where(ApiOperationRow.revision_id == previous_revision.id)).all()
                            ]
                        if previous_operations:
                            # 相同路由的 operationId 不可变更；相同 operationId 的换路由只产生 warning。
                            parsed = parse_and_normalize_openapi(filename, content, previous_operations)
                        next_number = int(session.scalar(select(func.max(ContractRevisionRow.revision_number)).where(ContractRevisionRow.service_id == service.id)) or 0) + 1
                        extension = filename.rsplit(".", 1)[-1].lower()
                        revision = ContractRevisionRow(
                            project_id=service.project_id,
                            service_id=service.id,
                            revision_number=next_number,
                            status="proposed",
                            source_filename=filename,
                            source_extension=extension,
                            source_version=parsed.source_version,
                            normalized_version=parsed.normalized_version,
                            profile_version="v1",
                            source_document=parsed.source_document,
                            normalized_document=parsed.document,
                            content_hash=parsed.content_hash,
                            validation_summary={"errors": parsed.errors, "warnings": parsed.warnings},
                            validation_result={"errors": parsed.errors, "warnings": parsed.warnings},
                            markdown_document=parsed.markdown,
                            created_by=created_by or "system",
                        )
                        session.add(revision)
                        session.flush()
                        for operation in parsed.operations:
                            session.add(
                                ApiOperationRow(
                                    project_id=service.project_id,
                                    service_id=service.id,
                                    revision_id=revision.id,
                                    method=operation.method,
                                    path=operation.path,
                                    operation_id=operation.operation_id,
                                    operation_hash=operation.operation_hash,
                                    summary=operation.summary,
                                    tags=operation.tags,
                                    deprecated=operation.deprecated,
                                    operation_json=operation.operation,
                                )
                            )
                        session.flush()
                        session.expunge(revision)
                        return revision, False
            except IntegrityError:
                # 并发请求可能在服务锁生效前竞争唯一约束，下一轮重新读取哈希或编号。
                with self.session_factory() as session:
                    existing = session.scalar(select(ContractRevisionRow).where(ContractRevisionRow.service_id == service_id, ContractRevisionRow.content_hash == parsed.content_hash))
                    if existing is not None:
                        session.expunge(existing)
                        return existing, True
                continue
        raise ContractRevisionConflictError("Revision 编号竞争未能完成", "revision_number_conflict")

    def get_revision(self, service_id: int, revision_number: int, project_id: int | None = None) -> dict[str, Any]:
        with self.session_factory() as session:
            query = select(ContractRevisionRow).where(ContractRevisionRow.service_id == service_id, ContractRevisionRow.revision_number == revision_number)
            if project_id is not None:
                query = query.where(ContractRevisionRow.project_id == project_id)
            revision = session.scalar(query)
            if revision is None:
                raise LookupError("Revision 不存在")
            operations = session.scalars(select(ApiOperationRow).where(ApiOperationRow.revision_id == revision.id).order_by(ApiOperationRow.path, ApiOperationRow.method)).all()
            return {
                **_revision_dict(revision),
                "operations": [_operation_dict(row) for row in operations],
                "operation_count": len(operations),
            }

    def publish(self, service_id: int, revision_number: int, expected_content_hash: str, project_id: int | None = None, published_by: str = "admin") -> tuple[ContractRevisionRow, bool]:
        with self.session_factory() as session:
            with session.begin():
                service_query = select(ContractServiceRow).where(ContractServiceRow.id == service_id).with_for_update()
                if project_id is not None:
                    service_query = service_query.where(ContractServiceRow.project_id == project_id)
                service = session.scalar(service_query)
                if service is None:
                    raise LookupError("服务不存在")
                query = select(ContractRevisionRow).where(ContractRevisionRow.service_id == service_id, ContractRevisionRow.revision_number == revision_number).with_for_update()
                if project_id is not None:
                    query = query.where(ContractRevisionRow.project_id == project_id)
                revision = session.scalar(query)
                if revision is None:
                    raise LookupError("Revision 不存在")
                if revision.content_hash != expected_content_hash:
                    raise ContractRevisionConflictError("expected_content_hash 不匹配", "content_hash_mismatch", {"expected_content_hash": expected_content_hash, "content_hash": revision.content_hash})
                if revision.status == "published":
                    service.current_published_revision_id = revision.id
                    session.expunge(revision)
                    return revision, True
                if revision.status != "proposed":
                    raise ContractRevisionConflictError("只有 proposed Revision 可以发布", "revision_not_publishable")
                current = None
                if service.current_published_revision_id is not None and service.current_published_revision_id != revision.id:
                    current = session.scalar(select(ContractRevisionRow).where(ContractRevisionRow.id == service.current_published_revision_id).with_for_update())
                if current is None:
                    current = session.scalar(select(ContractRevisionRow).where(ContractRevisionRow.service_id == service_id, ContractRevisionRow.status == "published").with_for_update())
                if current is not None and current.id != revision.id:
                    current.status = "superseded"
                revision.status = "published"
                revision.published_at = datetime.now(timezone.utc)
                revision.published_by = published_by or "admin"
                service.current_published_revision_id = revision.id
                session.flush()
                session.expunge(revision)
                return revision, False


def _service_dict(row: ContractServiceRow) -> dict[str, Any]:
    return {"id": row.id, "project_id": row.project_id, "service_key": row.service_key, "name": row.name, "description": row.description, "current_published_revision_id": row.current_published_revision_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}


def _revision_summary(row: ContractRevisionRow) -> dict[str, Any]:
    return {"id": row.id, "revision_number": row.revision_number, "status": row.status, "content_hash": row.content_hash, "source_version": row.source_version, "normalized_version": row.normalized_version, "profile_version": row.profile_version, "operation_count": 0, "created_by": row.created_by, "published_by": row.published_by, "created_at": _iso(row.created_at), "published_at": _iso(row.published_at)}


def _revision_dict(row: ContractRevisionRow) -> dict[str, Any]:
    validation = row.validation_summary or row.validation_result or {}
    return {"id": row.id, "project_id": row.project_id, "service_id": row.service_id, "revision_number": row.revision_number, "status": row.status, "source_filename": row.source_filename, "source_extension": row.source_extension, "source_version": row.source_version, "normalized_version": row.normalized_version, "profile_version": row.profile_version, "content_hash": row.content_hash, "validation": validation, "validation_result": validation, "validation_summary": validation, "warnings": validation.get("warnings", []), "source_document": row.source_document, "normalized_document": row.normalized_document, "document": row.normalized_document, "markdown_document": row.markdown_document, "created_by": row.created_by, "published_by": row.published_by, "created_at": _iso(row.created_at), "published_at": _iso(row.published_at)}


def _operation_dict(row: ApiOperationRow) -> dict[str, Any]:
    return {"id": row.id, "method": row.method, "path": row.path, "operation_id": row.operation_id, "operationId": row.operation_id, "operation_hash": row.operation_hash, "summary": row.summary, "tags": row.tags, "deprecated": row.deprecated, "operation": row.operation_json}


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


__all__ = ["ContractRevisionService", "ContractRevisionConflictError", "OpenAPIContractError"]
