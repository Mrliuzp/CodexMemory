from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func as sa_func, select, text
from sqlalchemy.orm import Session

from codex_memory.auth import PermissionDenied, ProjectAccessDenied, authenticate_bearer
from codex_memory.config import Settings
from codex_memory.db import create_engine_from_url, create_session_factory
from codex_memory.db_models import (
    ApiKeyRow,
    AuditLogRow,
    Base,
    MemoryEmbeddingRow,
    MemoryRow,
    MemoryVersionRow,
    MessageRow,
    ProjectRow,
    SessionRow,
)
from codex_memory.v11_models import DailyTokenUsageRow
from codex_memory.v11_candidates import (
    CandidateEvidenceRow,
    CandidatePolicyResultRow,
    MemoryCandidateRow,
)
from codex_memory.v11_embedding import EmbeddingProfileRow, EmbeddingProfileService
from codex_memory.v11_flags import (
    ProjectFeatureFlagRow,
    ProjectPolicyService,
    ProjectRetrievalProfileRow,
)
from codex_memory.v11_models import (
    JobAttemptRow,
    MemoryChunkRow,
    MemoryEmbeddingVectorRow,
    MemorySearchDocumentRow,
    OutboxEventRow,
    ProcessingJobRow,
    ProjectProcessingPolicyRow,
    RetrievalAuditRow,
    SecurityAuditRow,
)

LOGGER = logging.getLogger("codex_memory.admin")

_settings = Settings.from_env()
_engine = create_engine_from_url(_settings.database_url)
session_factory = create_session_factory(_engine)


def get_db() -> Session:
    with session_factory() as session:
        yield session


def _project_or_404(db: Session, project_id: int) -> ProjectRow:
    row = db.get(ProjectRow, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return row


def _safe_json(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Base):
        return None
    return obj


def _row_dict(row: Any, *keys: str) -> dict[str, Any]:
    d = {}
    for k in keys:
        v = getattr(row, k, None)
        d[k] = _safe_json(v)
    return d


app = FastAPI(title="Codex Memory 管理后台", version="1.0.0")

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------


@app.get("/api/admin/health")
def admin_health():
    with session_factory() as db:
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        dialect = db.bind.dialect.name if db.bind is not None else "unknown"
        vector = "not-applicable"
        if dialect == "postgresql":
            try:
                vector = "ok" if db.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname='vector'")
                ).first() else "error"
            except Exception:
                vector = "error"

        project_count = db.scalar(sa_func.count(ProjectRow.id))
        message_count = db.scalar(sa_func.count(MessageRow.id))
        memory_count = db.scalar(sa_func.count(MemoryRow.id))
        job_pending = db.scalar(
            select(sa_func.count(ProcessingJobRow.id)).where(
                ProcessingJobRow.status == "pending"
            )
        )
        candidate_pending = db.scalar(
            select(sa_func.count(MemoryCandidateRow.id)).where(
                MemoryCandidateRow.status == "generated"
            )
        )
        outbox_pending = db.scalar(
            select(sa_func.count(OutboxEventRow.id)).where(
                OutboxEventRow.status == "pending"
            )
        )

        return {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "error",
            "vector": vector,
            "dialect": dialect,
            "projects": project_count or 0,
            "messages": message_count or 0,
            "memories": memory_count or 0,
            "jobs_pending": job_pending or 0,
            "candidates_pending": candidate_pending or 0,
            "outbox_pending": outbox_pending or 0,
            "embedding_dimension": _settings.embedding_dimension,
        }


# ---------------------------------------------------------------------------
# 仪表盘统计
# ---------------------------------------------------------------------------


@app.get("/api/admin/stats")
def admin_stats():
    with session_factory() as db:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        projects = db.execute(
            select(
                ProjectRow.id,
                ProjectRow.project_key,
                ProjectRow.name,
                ProjectRow.status,
                ProjectRow.created_at,
            ).order_by(ProjectRow.created_at.desc())
        ).all()

        project_list = []
        for p in projects:
            msg_count = db.scalar(
                select(sa_func.count(MessageRow.id)).where(
                    MessageRow.project_id == p.id
                )
            )
            mem_count = db.scalar(
                select(sa_func.count(MemoryRow.id)).where(
                    MemoryRow.project_id == p.id
                )
            )
            job_counts = db.execute(
                select(
                    ProcessingJobRow.status,
                    sa_func.count(ProcessingJobRow.id),
                ).where(ProcessingJobRow.project_id == p.id).group_by(
                    ProcessingJobRow.status
                )
            ).all()
            cand_count = db.scalar(
                select(sa_func.count(MemoryCandidateRow.id)).where(
                    MemoryCandidateRow.project_id == p.id
                )
            )
            flag_row = db.execute(
                select(ProjectFeatureFlagRow).where(
                    ProjectFeatureFlagRow.project_id == p.id
                )
            ).scalar_one_or_none()

            project_list.append(
                {
                    "id": p.id,
                    "project_key": p.project_key,
                    "name": p.name,
                    "status": p.status,
                    "created_at": _safe_json(p.created_at),
                    "messages": msg_count or 0,
                    "memories": mem_count or 0,
                    "candidates": cand_count or 0,
                    "jobs": {s: int(c) for s, c in job_counts} if job_counts else {},
                    "flags_enabled": sum(
                        1
                        for f in [
                            "memory_v11_enabled",
                            "server_outbox_enabled",
                            "lexical_retrieval_enabled",
                            "dense_retrieval_enabled",
                            "embedding_profile_v2_enabled",
                            "candidate_publish_enabled",
                        ]
                        if flag_row and getattr(flag_row, f, False)
                    )
                    if flag_row
                    else 0,
                }
            )

        recent_jobs = db.execute(
            select(
                ProcessingJobRow.id,
                ProcessingJobRow.job_type,
                ProcessingJobRow.status,
                ProcessingJobRow.created_at,
                ProjectRow.project_key,
            )
            .join(ProjectRow, ProcessingJobRow.project_id == ProjectRow.id)
            .where(ProcessingJobRow.created_at >= week_ago)
            .order_by(ProcessingJobRow.created_at.desc())
            .limit(20)
        ).all()

        global_job_status = db.execute(
            select(
                ProcessingJobRow.status,
                sa_func.count(ProcessingJobRow.id),
            ).group_by(ProcessingJobRow.status)
        ).all()

        global_candidate_status = db.execute(
            select(
                MemoryCandidateRow.status,
                sa_func.count(MemoryCandidateRow.id),
            ).group_by(MemoryCandidateRow.status)
        ).all()

        memory_by_level = db.execute(
            select(MemoryRow.level, sa_func.count(MemoryRow.id)).group_by(
                MemoryRow.level
            )
        ).all()

        recent_candidates = db.execute(
            select(
                MemoryCandidateRow.id,
                MemoryCandidateRow.title,
                MemoryCandidateRow.status,
                MemoryCandidateRow.level,
                MemoryCandidateRow.created_at,
                ProjectRow.project_key,
            )
            .join(ProjectRow, MemoryCandidateRow.project_id == ProjectRow.id)
            .where(MemoryCandidateRow.status.in_(["generated", "approved"]))
            .order_by(MemoryCandidateRow.created_at.desc())
            .limit(10)
        ).all()

        return {
            "projects": project_list,
            "recent_jobs": [
                {
                    "id": j.id,
                    "job_type": j.job_type,
                    "status": j.status,
                    "created_at": _safe_json(j.created_at),
                    "project_key": j.project_key,
                }
                for j in recent_jobs
            ],
            "job_status_counts": {s: int(c) for s, c in global_job_status},
            "candidate_status_counts": {
                s: int(c) for s, c in global_candidate_status
            },
            "memory_by_level": {s: int(c) for s, c in memory_by_level},
            "recent_candidates": [
                {
                    "id": c.id,
                    "title": c.title,
                    "status": c.status,
                    "level": c.level,
                    "created_at": _safe_json(c.created_at),
                    "project_key": c.project_key,
                }
                for c in recent_candidates
            ],
        }


# ---------------------------------------------------------------------------
# 项目
# ---------------------------------------------------------------------------


@app.get("/api/admin/projects")
def list_projects():
    with session_factory() as db:
        rows = db.execute(
            select(ProjectRow).order_by(ProjectRow.created_at.desc())
        ).scalars()
        return {
            "projects": [
                _row_dict(
                    r,
                    "id",
                    "project_key",
                    "name",
                    "repository",
                    "description",
                    "status",
                    "created_at",
                    "updated_at",
                )
                for r in rows
            ]
        }


@app.get("/api/admin/projects/{project_id}")
def get_project(project_id: int):
    with session_factory() as db:
        p = _project_or_404(db, project_id)
        msg_count = db.scalar(
            select(sa_func.count(MessageRow.id)).where(
                MessageRow.project_id == p.id
            )
        )
        mem_count = db.scalar(
            select(sa_func.count(MemoryRow.id)).where(
                MemoryRow.project_id == p.id
            )
        )
        cand_count = db.scalar(
            select(sa_func.count(MemoryCandidateRow.id)).where(
                MemoryCandidateRow.project_id == p.id
            )
        )

        job_summary = db.execute(
            select(
                ProcessingJobRow.status,
                sa_func.count(ProcessingJobRow.id),
            )
            .where(ProcessingJobRow.project_id == p.id)
            .group_by(ProcessingJobRow.status)
        ).all()

        mem_by_level = db.execute(
            select(MemoryRow.level, sa_func.count(MemoryRow.id))
            .where(MemoryRow.project_id == p.id)
            .group_by(MemoryRow.level)
        ).all()

        flag = db.execute(
            select(ProjectFeatureFlagRow).where(
                ProjectFeatureFlagRow.project_id == p.id
            )
        ).scalar_one_or_none()

        policy = db.execute(
            select(ProjectProcessingPolicyRow).where(
                ProjectProcessingPolicyRow.project_id == p.id
            )
        ).scalar_one_or_none()

        return {
            "project": _row_dict(
                p,
                "id",
                "project_key",
                "name",
                "repository",
                "description",
                "status",
                "created_at",
                "updated_at",
            ),
            "messages": msg_count or 0,
            "memories": mem_count or 0,
            "candidates": cand_count or 0,
            "jobs_by_status": {s: int(c) for s, c in job_summary},
            "memories_by_level": {s: int(c) for s, c in mem_by_level},
            "feature_flags": _row_dict(
                flag,
                "memory_v11_enabled",
                "server_outbox_enabled",
                "lexical_retrieval_enabled",
                "dense_retrieval_enabled",
                "embedding_profile_v2_enabled",
                "llm_shadow_enabled",
                "candidate_publish_enabled",
            )
            if flag
            else None,
            "processing_policy": _row_dict(
                policy,
                "remote_embedding_allowed",
                "remote_llm_allowed",
                "redaction_enabled",
                "failure_mode",
                "allowed_embedding_providers",
                "allowed_llm_providers",
                "data_residency_policy",
            )
            if policy
            else None,
        }


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------


@app.get("/api/admin/jobs")
def list_jobs(
    project_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    with session_factory() as db:
        q = select(
            ProcessingJobRow,
            ProjectRow.project_key,
        ).join(ProjectRow, ProcessingJobRow.project_id == ProjectRow.id)

        if project_id is not None:
            q = q.where(ProcessingJobRow.project_id == project_id)
        if status:
            q = q.where(ProcessingJobRow.status == status)
        if job_type:
            q = q.where(ProcessingJobRow.job_type == job_type)

        q = q.order_by(ProcessingJobRow.created_at.desc()).limit(limit)
        rows = db.execute(q).all()

        result = []
        for job_row, proj_key in rows:
            attempts = db.execute(
                select(JobAttemptRow)
                .where(JobAttemptRow.job_id == job_row.id)
                .order_by(JobAttemptRow.attempt_no)
            ).scalars()

            entry = _row_dict(
                job_row,
                "id",
                "job_key",
                "job_type",
                "aggregate_type",
                "aggregate_id",
                "status",
                "priority",
                "attempt_count",
                "max_attempts",
                "last_error_code",
                "last_error_message",
                "created_at",
                "updated_at",
                "next_attempt_at",
                "locked_by",
                "locked_at",
                "lease_expires_at",
                "heartbeat_at",
                "completed_at",
            )
            entry["project_key"] = proj_key
            entry["project_id"] = job_row.project_id
            entry["attempts"] = [
                _row_dict(
                    a,
                    "attempt_no",
                    "worker_id",
                    "outcome",
                    "error_code",
                    "started_at",
                    "ended_at",
                )
                for a in attempts
            ]
            result.append(entry)

        return {"jobs": result}


@app.post("/api/admin/jobs/{job_id}/retry")
def retry_job(job_id: int):
    with session_factory() as db:
        job = db.get(ProcessingJobRow, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if job.status not in {"dead", "retry_wait"}:
            raise HTTPException(
                status_code=409,
                detail=f"任务状态为 {job.status}，不可重试",
            )
        job.status = "pending"
        job.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None)
        job.last_error_code = None
        job.last_error_message = None
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        db.add(
            SecurityAuditRow(
                project_id=job.project_id,
                event_type="job_retried",
                subject_type="job",
                subject_id=str(job.id),
                reason_code="admin_retry",
                metadata_json={"job_key": job.job_key},
            )
        )
        db.commit()
        return {"id": job.id, "status": job.status}


# ---------------------------------------------------------------------------
# 候选记忆
# ---------------------------------------------------------------------------


@app.get("/api/admin/candidates")
def list_candidates(
    project_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    level: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    with session_factory() as db:
        q = select(
            MemoryCandidateRow,
            ProjectRow.project_key,
        ).join(ProjectRow, MemoryCandidateRow.project_id == ProjectRow.id)

        if project_id is not None:
            q = q.where(MemoryCandidateRow.project_id == project_id)
        if status:
            q = q.where(MemoryCandidateRow.status == status)
        elif status is None:
            q = q.where(MemoryCandidateRow.status != "shadow")
        if level:
            q = q.where(MemoryCandidateRow.level == level)

        q = q.order_by(MemoryCandidateRow.created_at.desc()).limit(limit)
        rows = db.execute(q).all()

        return {
            "candidates": [
                {
                    **_row_dict(
                        c,
                        "id",
                        "status",
                        "level",
                        "scope",
                        "memory_type",
                        "task_type",
                        "title",
                        "model",
                        "model_confidence",
                        "abstain",
                        "created_at",
                        "updated_at",
                    ),
                    "content": c.content,
                    "project_key": proj_key,
                    "project_id": c.project_id,
                    "published_memory_id": c.published_memory_id,
                }
                for c, proj_key in rows
            ]
        }


class ReviewRequest(BaseModel):
    decision: str
    reviewer: str | None = None
    reason: str | None = None


@app.post("/api/admin/candidates/{candidate_id}/review")
def review_candidate(candidate_id: int, payload: ReviewRequest):
    decision = payload.decision
    if decision not in {"approve", "reject"}:
        raise HTTPException(
            status_code=422, detail="decision 必须是 approve 或 reject"
        )
    with session_factory() as db:
        candidate = db.get(MemoryCandidateRow, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="候选记忆不存在")
        candidate.status = "rejected" if decision == "reject" else "approved"
        db.add(
            CandidatePolicyResultRow(
                candidate_id=candidate.id,
                policy_version="review-v1",
                decision=decision,
                reason_codes=(
                    [] if decision == "approve" else ["review_rejected"]
                ),
                checks={"reviewed": True},
                reviewer=payload.reviewer,
                reason=payload.reason,
            )
        )
        db.commit()
        return {"id": candidate.id, "status": candidate.status}


# ---------------------------------------------------------------------------
# 嵌入配置
# ---------------------------------------------------------------------------


@app.get("/api/admin/profiles")
def list_profiles():
    with session_factory() as db:
        rows = db.execute(
            select(EmbeddingProfileRow).order_by(
                EmbeddingProfileRow.created_at.desc()
            )
        ).scalars()
        return {
            "profiles": [
                _row_dict(
                    r,
                    "id",
                    "name",
                    "provider",
                    "model",
                    "dimension",
                    "similarity_metric",
                    "normalization",
                    "status",
                    "chunker_version",
                    "content_normalization_version",
                    "max_batch_size",
                    "max_tokens_per_input",
                    "created_at",
                    "updated_at",
                    "retired_at",
                )
                for r in rows
            ]
        }


class ProfileCreateRequest(BaseModel):
    name: str
    provider: str
    model: str
    dimension: int
    chunker_version: str = "v1"
    content_normalization_version: str = "v1"
    normalization: str = "l2"


@app.post("/api/admin/profiles")
def create_profile(payload: ProfileCreateRequest):
    try:
        svc = EmbeddingProfileService(session_factory)
        profile = svc.create_profile(
            name=payload.name,
            provider=payload.provider,
            model=payload.model,
            dimension=payload.dimension,
            chunker_version=payload.chunker_version,
            content_normalization_version=payload.content_normalization_version,
            normalization=payload.normalization,
        )
        return {
            "id": profile.id,
            "name": profile.name,
            "dimension": profile.dimension,
            "status": profile.status,
        }
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---------------------------------------------------------------------------
# 功能开关
# ---------------------------------------------------------------------------


@app.get("/api/admin/flags/{project_id}")
def get_flags(project_id: int):
    _project_or_404(get_db_impl(), project_id)
    with session_factory() as db:
        flag = db.execute(
            select(ProjectFeatureFlagRow).where(
                ProjectFeatureFlagRow.project_id == project_id
            )
        ).scalar_one_or_none()
        policy = db.execute(
            select(ProjectRetrievalProfileRow).where(
                ProjectRetrievalProfileRow.project_id == project_id
            )
        ).scalar_one_or_none()
        return {
            "project_id": project_id,
            "feature_flags": _row_dict(
                flag,
                "memory_v11_enabled",
                "server_outbox_enabled",
                "lexical_retrieval_enabled",
                "dense_retrieval_enabled",
                "embedding_profile_v2_enabled",
                "llm_shadow_enabled",
                "candidate_publish_enabled",
            )
            if flag
            else {
                k: False
                for k in [
                    "memory_v11_enabled",
                    "server_outbox_enabled",
                    "lexical_retrieval_enabled",
                    "dense_retrieval_enabled",
                    "embedding_profile_v2_enabled",
                    "llm_shadow_enabled",
                    "candidate_publish_enabled",
                ]
            },
            "retrieval_profile": _row_dict(
                policy,
                "active_embedding_profile_id",
                "canary_embedding_profile_id",
                "canary_percent",
                "hybrid_search_enabled",
                "fallback_mode",
                "global_result_limit",
            )
            if policy
            else {
                "active_embedding_profile_id": None,
                "canary_embedding_profile_id": None,
                "canary_percent": 0,
                "hybrid_search_enabled": False,
                "fallback_mode": "lexical_only",
                "global_result_limit": 3,
            },
        }


class FlagUpdateRequest(BaseModel):
    flags: dict[str, bool]


@app.put("/api/admin/flags/{project_id}")
def update_flags(project_id: int, payload: FlagUpdateRequest):
    p = _project_or_404(get_db_impl(), project_id)
    try:
        svc = ProjectPolicyService(session_factory)
        flags = svc.update_flags(p.id, **payload.flags)
        return {
            "project_id": project_id,
            "flags": {
                name: getattr(flags, name)
                for name in (
                    "memory_v11_enabled",
                    "server_outbox_enabled",
                    "lexical_retrieval_enabled",
                    "dense_retrieval_enabled",
                    "embedding_profile_v2_enabled",
                    "llm_shadow_enabled",
                    "candidate_publish_enabled",
                )
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def get_db_impl() -> Session:
    """Helper to open a session for project lookup."""
    with session_factory() as db:
        return db


# ---------------------------------------------------------------------------
# 记忆
# ---------------------------------------------------------------------------


@app.get("/api/admin/memories")
def list_memories(
    project_id: int | None = Query(default=None),
    level: str | None = Query(default=None),
    memory_type: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    with session_factory() as db:
        q = select(MemoryRow)

        if project_id is not None:
            q = q.where(MemoryRow.project_id == project_id)
        if level:
            q = q.where(MemoryRow.level == level)
        if memory_type:
            q = q.where(MemoryRow.memory_type == memory_type)
        if scope:
            q = q.where(MemoryRow.scope == scope)
        if search:
            q = q.where(MemoryRow.title.ilike(f"%{search}%"))

        total = db.scalar(
            select(sa_func.count()).select_from(q.subquery())
        )
        rows = db.scalars(
            q.order_by(MemoryRow.updated_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()

        return {
            "memories": [
                {
                    **_row_dict(
                        r,
                        "id",
                        "project_id",
                        "level",
                        "memory_type",
                        "title",
                        "status",
                        "confidence",
                        "scope",
                        "source_kind",
                        "review_status",
                        "usage_count",
                        "created_at",
                        "updated_at",
                        "last_used_at",
                    ),
                    "content": r.content,
                }
                for r in rows
            ],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        }


# ---------------------------------------------------------------------------
# L0 原始日志
# ---------------------------------------------------------------------------


@app.get("/api/admin/logs")
def list_logs(
    project_id: int | None = Query(default=None),
    session_id: int | None = Query(default=None),
    role: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    with session_factory() as db:
        q = select(
            MessageRow,
            ProjectRow.project_key,
            SessionRow.session_key,
        ).join(ProjectRow, MessageRow.project_id == ProjectRow.id).join(
            SessionRow, MessageRow.session_id == SessionRow.id
        )

        if project_id is not None:
            q = q.where(MessageRow.project_id == project_id)
        if session_id is not None:
            q = q.where(MessageRow.session_id == session_id)
        if role:
            q = q.where(MessageRow.role == role)

        total = db.scalar(
            select(sa_func.count()).select_from(q.subquery())
        )
        rows = db.execute(
            q.order_by(MessageRow.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()

        return {
            "logs": [
                {
                    **_row_dict(
                        m,
                        "id",
                        "event_key",
                        "role",
                        "content",
                        "content_hash",
                        "source",
                        "metadata_json",
                        "created_at",
                        "occurred_at",
                        "ingestion_version",
                        "project_id",
                        "session_id",
                    ),
                    "project_key": pk,
                    "session_key": sk,
                }
                for m, pk, sk in rows
            ],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        }


# ---------------------------------------------------------------------------
# 令牌用量
# ---------------------------------------------------------------------------


@app.get("/api/admin/token-usage")
def get_token_usage(
    project_id: int | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=90),
):
    try:
        with session_factory() as db:
            since = date.today() - timedelta(days=days - 1)
            q = select(DailyTokenUsageRow)
            if project_id is not None:
                q = q.where(DailyTokenUsageRow.project_id == project_id)
            q = q.where(DailyTokenUsageRow.usage_date >= since)
            rows = db.execute(
                q.order_by(DailyTokenUsageRow.usage_date.desc())
            ).scalars()
            return {
                "usage": [
                    _row_dict(
                        r,
                        "id",
                        "project_id",
                        "usage_date",
                        "token_type",
                        "tokens_used",
                        "updated_at",
                    )
                    for r in rows
                ]
            }
    except Exception:
        return {"usage": []}


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------


@app.get("/api/admin/audit-logs")
def list_audit_logs(
    project_id: int | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    with session_factory() as db:
        q = select(SecurityAuditRow)

        if project_id is not None:
            q = q.where(SecurityAuditRow.project_id == project_id)
        if event_type:
            q = q.where(SecurityAuditRow.event_type == event_type)

        total = db.scalar(
            select(sa_func.count()).select_from(q.subquery())
        )
        rows = db.scalars(
            q.order_by(SecurityAuditRow.id.desc()).offset(offset).limit(limit)
        ).all()

        return {
            "audit_logs": [
                _row_dict(
                    r,
                    "id",
                    "project_id",
                    "event_type",
                    "subject_type",
                    "subject_id",
                    "reason_code",
                    "metadata_json",
                )
                for r in rows
            ],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        }


# ---------------------------------------------------------------------------
# SPA 回退：为所有前端路由提供 index.html
# ---------------------------------------------------------------------------


@app.get("/")
@app.get("/{path:path}")
async def serve_spa(path: str = ""):
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="未找到资源")
    index = STATIC / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    return JSONResponse(
        {"status": "ok", "message": "Codex Memory 管理 API"},
        status_code=200,
    )


__all__ = ["app"]
