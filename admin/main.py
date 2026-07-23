from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
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

import hashlib
import hmac
import json
import time
import base64
import os

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

# CORS 中间件 -- 允许开发跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 管理后台认证配置（默认密码可通过环境变量覆盖）
def _load_admin_users() -> dict[str, str]:
    raw = os.environ.get("CODEX_MEMORY_ADMIN_USERS", "")
    users = {}
    if raw:
        for entry in raw.split(","):
            if ":" in entry:
                u, p = entry.split(":", 1)
                users[u.strip()] = hashlib.sha256(p.strip().encode()).hexdigest()
    if not users:
        u = os.environ.get(
            "CODEX_MEMORY_ADMIN_USER",
            os.environ.get("CODEX_MEMORY_ADMIN_USERNAME", "admin"),
        )
        p = os.environ.get("CODEX_MEMORY_ADMIN_PASSWORD", "admin")
        users[u] = hashlib.sha256(p.encode()).hexdigest()
    return users

_admin_users = _load_admin_users()
JWT_SECRET = os.environ.get(
    "CODEX_MEMORY_JWT_SECRET",
    hashlib.sha256(_settings.database_url.encode()).hexdigest()
)


# ---------------------------------------------------------------------------
# JWT 工具函数
# ---------------------------------------------------------------------------


def _jwt_encode(payload: dict) -> str:
    """创建 JWT token（HS256）。"""
    header = {"alg": "HS256", "typ": "JWT"}
    def _b64(data: dict) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(data, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
    hdr = _b64(header)
    pld = _b64(payload)
    sig = hmac.new(
        JWT_SECRET.encode(), f"{hdr}.{pld}".encode(), hashlib.sha256
    ).digest()
    return f"{hdr}.{pld}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def _jwt_decode(token: str) -> dict | None:
    """验证并解码 JWT token，返回 payload 或 None。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        expected = hmac.new(
            JWT_SECRET.encode(), f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256
        ).digest()
        raw_sig = parts[2] + "=" * (4 - len(parts[2]) % 4)
        actual = base64.urlsafe_b64decode(raw_sig)
        if not hmac.compare_digest(expected, actual):
            return None
        raw_pld = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw_pld))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 会话跟踪
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}

def _track_session(token: str, username: str) -> str:
    key = hashlib.sha256(token.encode()).hexdigest()[:16]
    _sessions[key] = {
        "username": username,
        "login_time": _safe_json(datetime.now(timezone.utc)),
        "last_active": _safe_json(datetime.now(timezone.utc)),
    }
    return key

def _remove_session(token: str) -> None:
    key = hashlib.sha256(token.encode()).hexdigest()[:16]
    _sessions.pop(key, None)

# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/admin/login")
def admin_login(payload: LoginRequest):
    """管理员登录，返回 JWT token。"""
    pwd_hash = hashlib.sha256(payload.password.encode()).hexdigest()
    if _admin_users.get(payload.username) != pwd_hash:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = _jwt_encode({
        "sub": payload.username,
        "role": "admin",
        "iat": int(time.time()),
        "exp": int(time.time()) + 86400,
    })
    session_id = _track_session(token, payload.username)
    return {"access_token": token, "token_type": "bearer", "session_id": session_id}


@app.get("/api/admin/me")
def admin_me(authorization: str = Header(default="")):
    """返回当前登录用户信息。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未授权")
    payload = _jwt_decode(authorization[7:])
    if payload is None:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    return {
        "data": {
            "username": payload["sub"],
            "role": payload.get("role", "admin"),
            "permissions": ["admin", "read"],
        }
    }


# ---------------------------------------------------------------------------
# 仪表盘
# ---------------------------------------------------------------------------


@app.get("/api/admin/dashboard")
def admin_dashboard():
    """聚合仪表盘统计数据。"""
    with session_factory() as db:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        total_records = db.scalar(select(sa_func.count(MessageRow.id))) or 0
        total_candidates = db.scalar(
            select(sa_func.count(MemoryCandidateRow.id))
        ) or 0
        total_memories = db.scalar(select(sa_func.count(MemoryRow.id))) or 0
        total_jobs = db.scalar(
            select(sa_func.count(ProcessingJobRow.id))
        ) or 0
        pending_jobs = db.scalar(
            select(sa_func.count(ProcessingJobRow.id)).where(
                ProcessingJobRow.status == "pending"
            )
        ) or 0

        records_7d = db.scalar(
            select(sa_func.count(MessageRow.id)).where(
                MessageRow.created_at >= week_ago
            )
        ) or 0
        memories_7d = db.scalar(
            select(sa_func.count(MemoryRow.id)).where(
                MemoryRow.created_at >= week_ago
            )
        ) or 0

        # 每日趋势数据（图表用）
        days_14 = now - timedelta(days=13)
        daily_records_rows = db.execute(
            select(sa_func.date(MessageRow.created_at).label("date"),
                   sa_func.count(MessageRow.id).label("count"))
            .where(MessageRow.created_at >= days_14)
            .group_by(sa_func.date(MessageRow.created_at))
            .order_by(sa_func.date(MessageRow.created_at))
        ).all()
        daily_memories_rows = db.execute(
            select(sa_func.date(MemoryRow.created_at).label("date"),
                   sa_func.count(MemoryRow.id).label("count"))
            .where(MemoryRow.created_at >= days_14)
            .group_by(sa_func.date(MemoryRow.created_at))
            .order_by(sa_func.date(MemoryRow.created_at))
        ).all()

        per_project = []
        projects = db.execute(
            select(ProjectRow).order_by(ProjectRow.created_at.desc())
        ).scalars().all()
        for p in projects:
            pm = db.scalar(
                select(sa_func.count(MessageRow.id)).where(MessageRow.project_id == p.id)
            ) or 0
            pmem = db.scalar(
                select(sa_func.count(MemoryRow.id)).where(MemoryRow.project_id == p.id)
            ) or 0
            per_project.append({
                "id": p.id,
                "project_key": p.project_key,
                "name": p.name,
                "messages": pm,
                "memories": pmem,
            })

        return {
            "data": {
                "raw_records": total_records,
                "candidates": total_candidates,
                "memories": total_memories,
                "jobs": total_jobs,
                "pending_jobs": pending_jobs,
                "records_7d": records_7d,
                "memories_7d": memories_7d,
                "projects": per_project,
                "daily_records": [{"date": str(r.date), "count": r.count} for r in daily_records_rows],
                "daily_memories": [{"date": str(r.date), "count": r.count} for r in daily_memories_rows],
            }
        }


# ---------------------------------------------------------------------------
# 系统状态
# ---------------------------------------------------------------------------


@app.get("/api/admin/system/status")
def admin_system_status():
    """返回系统运行状态。"""
    with session_factory() as db:
        try:
            db.execute(text("SELECT 1"))
            database = "ok"
        except Exception:
            database = "error"

        pending_jobs = db.scalar(
            select(sa_func.count(ProcessingJobRow.id)).where(
                ProcessingJobRow.status == "pending"
            )
        ) or 0
        server_outbox = db.scalar(
            select(sa_func.count(OutboxEventRow.id)).where(
                OutboxEventRow.status == "pending"
            )
        ) or 0
        dead_letters = db.scalar(
            select(sa_func.count(OutboxEventRow.id)).where(
                OutboxEventRow.status == "dead"
            )
        ) or 0

        return {
            "data": {
                "database": database,
                "migration_schema": "ok",
                "pending_jobs": pending_jobs,
                "server_outbox": server_outbox,
                "dead_letters": dead_letters,
                "latest_migration": "0010",
                "maintenance": {"enabled": False},
            }
        }


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

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
# 作业批量操作
# ---------------------------------------------------------------------------


class BatchOperationRequest(BaseModel):
    job_ids: list[int] | None = None
    project_id: int | None = None
    status_filter: str | None = None


@app.post("/api/admin/jobs/batch-retry")
def batch_retry_jobs(payload: BatchOperationRequest):
    """批量重试作业（dead / retry_wait 状态）。"""
    with session_factory() as db:
        q = select(ProcessingJobRow)
        if payload.job_ids:
            q = q.where(ProcessingJobRow.id.in_(payload.job_ids))
        if payload.project_id is not None:
            q = q.where(ProcessingJobRow.project_id == payload.project_id)
        if payload.status_filter:
            q = q.where(ProcessingJobRow.status == payload.status_filter)
        else:
            q = q.where(ProcessingJobRow.status.in_(["dead", "retry_wait"]))
        jobs = db.scalars(q).all()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        retried_ids = []
        for job in jobs:
            job.status = "pending"
            job.next_attempt_at = now
            job.last_error_code = None
            job.last_error_message = None
            job.locked_by = None
            job.locked_at = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            retried_ids.append(job.id)
        db.commit()
        return {"retried": len(retried_ids), "job_ids": retried_ids}


@app.post("/api/admin/jobs/batch-cancel")
def batch_cancel_jobs(payload: BatchOperationRequest):
    """批量取消作业（pending / retry_wait 状态 → dead）。"""
    with session_factory() as db:
        q = select(ProcessingJobRow)
        if payload.job_ids:
            q = q.where(ProcessingJobRow.id.in_(payload.job_ids))
        if payload.project_id is not None:
            q = q.where(ProcessingJobRow.project_id == payload.project_id)
        if payload.status_filter:
            q = q.where(ProcessingJobRow.status == payload.status_filter)
        else:
            q = q.where(ProcessingJobRow.status.in_(["pending", "retry_wait"]))
        jobs = db.scalars(q).all()
        cancelled_ids = []
        for job in jobs:
            if job.status in ("pending", "retry_wait"):
                job.status = "dead"
                job.last_error_message = "cancelled_by_admin"
                cancelled_ids.append(job.id)
        db.commit()
        return {"cancelled": len(cancelled_ids), "job_ids": cancelled_ids}


class CleanupRequest(BaseModel):
    older_than_days: int = 30
    status: str = "completed"


@app.post("/api/admin/jobs/cleanup")
def cleanup_jobs(payload: CleanupRequest):
    """清理指定状态的老作业。"""
    with session_factory() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(days=payload.older_than_days)
        q = select(ProcessingJobRow).where(
            ProcessingJobRow.status == payload.status,
            ProcessingJobRow.completed_at < cutoff,
        )
        jobs = db.scalars(q).all()
        deleted_ids = [j.id for j in jobs]
        for job in jobs:
            db.delete(job)
        db.commit()
        return {"deleted": len(deleted_ids), "job_ids": deleted_ids}


# ---------------------------------------------------------------------------
# 候选记忆
# ---------------------------------------------------------------------------

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





class ProfileActivateRequest(BaseModel):
    project_id: int
    profile_id: int


@app.post("/api/admin/profiles/activate")
def activate_profile(payload: ProfileActivateRequest):
    """激活嵌入配置到项目。"""
    _project_or_404(get_db_impl(), payload.project_id)
    try:
        svc = ProjectPolicyService(session_factory)
        setting = svc.set_active_profile(payload.project_id, payload.profile_id)
        return {"project_id": payload.project_id, "active_embedding_profile_id": setting.active_embedding_profile_id}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))


class CanaryRequest(BaseModel):
    project_id: int
    profile_id: int
    percent: int = 10


@app.post("/api/admin/profiles/canary")
def set_canary_profile(payload: CanaryRequest):
    """设置项目的金丝雀嵌入配置。"""
    _project_or_404(get_db_impl(), payload.project_id)
    if not 0 <= payload.percent <= 100:
        raise HTTPException(status_code=422, detail="百分比必须在 0-100 之间")
    try:
        svc = ProjectPolicyService(session_factory)
        setting = svc.set_canary_profile(payload.project_id, payload.profile_id, payload.percent)
        return {"project_id": payload.project_id, "canary_embedding_profile_id": setting.canary_embedding_profile_id, "canary_percent": setting.canary_percent}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))


class RollbackRequest(BaseModel):
    project_id: int
    reason: str = "admin_rollback"


@app.post("/api/admin/profiles/rollback")
def rollback_profile(payload: RollbackRequest):
    """回滚项目的嵌入配置到上一个有效版本。"""
    _project_or_404(get_db_impl(), payload.project_id)
    try:
        svc = ProjectPolicyService(session_factory)
        setting = svc.rollback_profile(payload.project_id, payload.reason)
        return {"project_id": payload.project_id, "active_embedding_profile_id": setting.active_embedding_profile_id}
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




class PolicyUpdateRequest(BaseModel):
    """项目处理策略更新请求。"""
    remote_embedding_allowed: bool | None = None
    remote_llm_allowed: bool | None = None
    redaction_enabled: bool | None = None
    failure_mode: str | None = None
    allowed_embedding_providers: list[str] | None = None
    allowed_llm_providers: list[str] | None = None
    data_residency_policy: dict | None = None


@app.put("/api/admin/projects/{project_id}/policy")
def update_project_policy(project_id: int, payload: PolicyUpdateRequest):
    """更新项目处理策略。"""
    from codex_memory.v11_models import ProjectProcessingPolicyRow
    p = _project_or_404(get_db_impl(), project_id)
    with session_factory() as db:
        policy = db.execute(
            select(ProjectProcessingPolicyRow).where(
                ProjectProcessingPolicyRow.project_id == p.id
            )
        ).scalar_one_or_none()
        if policy is None:
            policy = ProjectProcessingPolicyRow(project_id=p.id)
            db.add(policy)
        updates = payload.model_dump(exclude_none=True)
        for key, value in updates.items():
            setattr(policy, key, value)
        db.commit()
        return {
            "policy": _row_dict(
                policy,
                "remote_embedding_allowed",
                "remote_llm_allowed",
                "redaction_enabled",
                "failure_mode",
                "allowed_embedding_providers",
                "allowed_llm_providers",
                "daily_embedding_token_budget",
                "daily_llm_token_budget",
                "data_residency_policy",
            )
        }

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
# 记忆管理（编辑/删除/变更层级）
# ---------------------------------------------------------------------------


class MemoryUpdateRequest(BaseModel):
    title: str | None = None
    content: dict | None = None
    status: str | None = None


@app.put("/api/admin/memories/{memory_id}")
def update_memory(memory_id: int, payload: MemoryUpdateRequest):
    """编辑记忆（标题、内容、状态）。"""
    with session_factory() as db:
        memory = db.get(MemoryRow, memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail="记忆不存在")
        if payload.title is not None:
            memory.title = payload.title
        if payload.content is not None:
            memory.content = payload.content
        if payload.status is not None:
            valid_statuses = {"active", "archived", "draft"}
            if payload.status not in valid_statuses:
                raise HTTPException(status_code=422, detail=f"状态值无效，有效值: {valid_statuses}")
            memory.status = payload.status
        db.commit()
        return {"id": memory.id, "status": memory.status, "title": memory.title}


@app.delete("/api/admin/memories/{memory_id}")
def delete_memory(memory_id: int):
    """删除记忆。"""
    with session_factory() as db:
        memory = db.get(MemoryRow, memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail="记忆不存在")
        db.delete(memory)
        db.commit()
        return {"deleted": memory_id}


class MemoryLevelRequest(BaseModel):
    level: str


@app.post("/api/admin/memories/{memory_id}/level")
def change_memory_level(memory_id: int, payload: MemoryLevelRequest):
    """变更记忆层级（L1 ↔ L2 ↔ L3）。"""
    valid_levels = {"L1", "L2", "L3"}
    if payload.level not in valid_levels:
        raise HTTPException(status_code=422, detail=f"层级无效，有效值: {valid_levels}")
    with session_factory() as db:
        memory = db.get(MemoryRow, memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail="记忆不存在")
        memory.level = payload.level
        db.commit()
        return {"id": memory.id, "level": memory.level}
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
# 令牌预算管理
# ---------------------------------------------------------------------------


@app.get("/api/admin/budgets")
def list_budgets():
    """列出所有项目的预算配置。"""
    with session_factory() as db:
        rows = db.execute(
            select(
                ProjectRow.id,
                ProjectRow.project_key,
                ProjectProcessingPolicyRow.daily_embedding_token_budget,
                ProjectProcessingPolicyRow.daily_llm_token_budget,
            )
            .outerjoin(
                ProjectProcessingPolicyRow,
                ProjectRow.id == ProjectProcessingPolicyRow.project_id,
            )
            .order_by(ProjectRow.id)
        ).all()
        budgets = []
        for row in rows:
            budgets.append({
                "project_id": row.id,
                "project_key": row.project_key,
                "daily_embedding_token_budget": row.daily_embedding_token_budget,
                "daily_llm_token_budget": row.daily_llm_token_budget,
            })
        return {"budgets": budgets}


class BudgetUpdateRequest(BaseModel):
    daily_embedding_token_budget: int | None = None
    daily_llm_token_budget: int | None = None


@app.put("/api/admin/budgets/{project_id}")
def update_budget(project_id: int, payload: BudgetUpdateRequest):
    """更新项目的预算限制。"""
    _project_or_404(get_db_impl(), project_id)
    with session_factory() as db:
        policy = db.execute(
            select(ProjectProcessingPolicyRow).where(
                ProjectProcessingPolicyRow.project_id == project_id
            )
        ).scalar_one_or_none()
        if policy is None:
            policy = ProjectProcessingPolicyRow(project_id=project_id)
            db.add(policy)
        if payload.daily_embedding_token_budget is not None:
            policy.daily_embedding_token_budget = payload.daily_embedding_token_budget
        if payload.daily_llm_token_budget is not None:
            policy.daily_llm_token_budget = payload.daily_llm_token_budget
        db.commit()
        return {
            "project_id": project_id,
            "daily_embedding_token_budget": policy.daily_embedding_token_budget,
            "daily_llm_token_budget": policy.daily_llm_token_budget,
        }


@app.get("/api/admin/budgets/summary")
def budget_summary():
    """返回各项目预算使用摘要。"""
    today = date.today()
    with session_factory() as db:
        rows = db.execute(
            select(
                ProjectRow.id,
                ProjectRow.project_key,
                ProjectProcessingPolicyRow.daily_embedding_token_budget,
                ProjectProcessingPolicyRow.daily_llm_token_budget,
            )
            .outerjoin(
                ProjectProcessingPolicyRow,
                ProjectRow.id == ProjectProcessingPolicyRow.project_id,
            )
        ).all()
        summary = []
        for row in rows:
            embed_used = db.scalar(
                select(sa_func.coalesce(sa_func.sum(DailyTokenUsageRow.tokens_used), 0))
                .where(
                    DailyTokenUsageRow.project_id == row.id,
                    DailyTokenUsageRow.usage_date == today,
                    DailyTokenUsageRow.token_type == "embedding",
                )
            ) or 0
            llm_used = db.scalar(
                select(sa_func.coalesce(sa_func.sum(DailyTokenUsageRow.tokens_used), 0))
                .where(
                    DailyTokenUsageRow.project_id == row.id,
                    DailyTokenUsageRow.usage_date == today,
                    DailyTokenUsageRow.token_type == "llm",
                )
            ) or 0

            embed_budget = row.daily_embedding_token_budget or 0
            llm_budget = row.daily_llm_token_budget or 0

            embed_pct = round(embed_used / embed_budget * 100, 1) if embed_budget > 0 else 0
            llm_pct = round(llm_used / llm_budget * 100, 1) if llm_budget > 0 else 0

            over_budget = embed_used > embed_budget or llm_used > llm_budget
            alert_level = "critical" if over_budget else "warning" if max(embed_pct, llm_pct) > 80 else "ok"

            summary.append({
                "project_id": row.id,
                "project_key": row.project_key,
                "embedding_used_today": embed_used,
                "embedding_budget": embed_budget,
                "embedding_pct": embed_pct,
                "llm_used_today": llm_used,
                "llm_budget": llm_budget,
                "llm_pct": llm_pct,
                "over_budget": over_budget,
                "alert_level": alert_level,
            })
        return {"summary": summary}
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
# 只读数据（Records API - 供前端 /records 页面使用）
# ---------------------------------------------------------------------------


@app.get("/api/admin/raw-records")
def list_raw_records(
    project_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """原始 L0 消息记录，供 Records 页面使用。"""
    with session_factory() as db:
        q = select(MessageRow)
        if project_id is not None:
            q = q.where(MessageRow.project_id == project_id)
        total = db.scalar(select(sa_func.count()).select_from(q.subquery())) or 0
        rows = db.scalars(
            q.order_by(MessageRow.created_at.desc()).offset(offset).limit(limit)
        ).all()
        return {
            "data": [
                {
                    "id": r.id,
                    "project_id": r.project_id,
                    "event_key": r.event_key,
                    "role": r.role,
                    "content": r.content,
                    "source": r.source,
                    "created_at": _safe_json(r.created_at),
                }
                for r in rows
            ],
            "total": total,
        }


@app.get("/api/admin/outbox-events")
def list_outbox_events(
    project_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """Outbox 事件列表。"""
    with session_factory() as db:
        q = select(OutboxEventRow)
        if project_id is not None:
            q = q.where(OutboxEventRow.project_id == project_id)
        if status:
            q = q.where(OutboxEventRow.status == status)
        total = db.scalar(select(sa_func.count()).select_from(q.subquery())) or 0
        rows = db.scalars(
            q.order_by(OutboxEventRow.id.desc()).offset(offset).limit(limit)
        ).all()
        return {
            "data": [
                {
                    "id": r.id,
                    "project_id": r.project_id,
                    "event_type": r.event_type,
                    "status": r.status,
                    "attempt_count": r.attempt_count,
                    "created_at": _safe_json(r.created_at),
                }
                for r in rows
            ],
            "total": total,
        }


@app.get("/api/admin/retrieval-audits")
def list_retrieval_audits(
    project_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """检索审计列表。"""
    with session_factory() as db:
        q = select(RetrievalAuditRow)
        if project_id is not None:
            q = q.where(RetrievalAuditRow.project_id == project_id)
        total = db.scalar(select(sa_func.count()).select_from(q.subquery())) or 0
        rows = db.scalars(
            q.order_by(RetrievalAuditRow.id.desc()).offset(offset).limit(limit)
        ).all()
        return {
            "data": [
                {
                    "id": r.id,
                    "project_id": r.project_id,
                    "retrieval_mode": r.retrieval_mode,
                    "degraded": r.degraded,
                    "degraded_reason": r.degraded_reason,
                    "latency_ms": r.latency_ms,
                    "query_type": r.query_type,
                    "created_at": _safe_json(r.created_at),
                }
                for r in rows
            ],
            "total": total,
        }


@app.get("/api/admin/audit-events")
def list_audit_events(
    project_id: int | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """审计事件列表（SecurityAuditRow 别名）。"""
    with session_factory() as db:
        q = select(SecurityAuditRow)
        if project_id is not None:
            q = q.where(SecurityAuditRow.project_id == project_id)
        if event_type:
            q = q.where(SecurityAuditRow.event_type == event_type)
        total = db.scalar(select(sa_func.count()).select_from(q.subquery())) or 0
        rows = db.scalars(
            q.order_by(SecurityAuditRow.id.desc()).offset(offset).limit(limit)
        ).all()
        return {
            "data": [
                {
                    "id": r.id,
                    "project_id": r.project_id,
                    "event_type": r.event_type,
                    "subject_type": r.subject_type,
                    "subject_id": r.subject_id,
                    "created_at": _safe_json(r.created_at),
                }
                for r in rows
            ],
            "total": total,
        }


# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# 数据导入与导出
# ---------------------------------------------------------------------------


class ImportItem(BaseModel):
    type: str  # "memory" or "message"
    level: str | None = "L2"
    memory_type: str | None = "knowledge"
    title: str | None = None
    content: Any = None
    scope: str | None = "project"
    role: str | None = None
    event_key: str | None = None
    session_key: str | None = None
    occurred_at: datetime | None = None


class ImportRequest(BaseModel):
    project_key: str
    items: list[ImportItem]


@app.post("/api/admin/import/preview")
def import_preview(payload: ImportRequest):
    """预览导入内容，返回统计信息但不实际写入。"""
    _project_or_404(get_db_impl(), payload.project_key)
    mem_count = sum(1 for i in payload.items if i.type == "memory")
    msg_count = sum(1 for i in payload.items if i.type == "message")
    errors = []
    for idx, item in enumerate(payload.items):
        if item.type not in ("memory", "message"):
            errors.append(f"第 {idx+1} 项: 无效类型 '{item.type}'，仅支持 memory/message")
        if item.type == "memory" and not item.title:
            errors.append(f"第 {idx+1} 项: 记忆项缺少标题")
        if item.type == "message" and not item.content:
            errors.append(f"第 {idx+1} 项: 消息项缺少内容")
    return {"project_key": payload.project_key, "total_items": len(payload.items),
            "memories": mem_count, "messages": msg_count, "errors": errors}


@app.post("/api/admin/import/execute")
def import_execute(payload: ImportRequest):
    """执行导入，将数据写入数据库。"""
    from datetime import datetime, timezone
    p = _project_or_404(get_db_impl(), payload.project_key)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    memories_created = 0
    messages_created = 0
    errors = []
    with session_factory() as db:
        for idx, item in enumerate(payload.items):
            try:
                if item.type == "memory":
                    mem = MemoryRow(
                        project_id=p.id,
                        level=item.level or "L2",
                        memory_type=item.memory_type or "knowledge",
                        title=item.title or "",
                        content=item.content or {},
                        confidence=0.7,
                        status="active",
                        usage_count=0,
                        scope=item.scope or "project",
                        source_kind="import",
                        review_status="pending",
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(mem)
                    memories_created += 1
                elif item.type == "message":
                    msg = MessageRow(
                        project_id=p.id,
                        session_key=item.session_key or f"import-{p.project_key}-{idx}",
                        event_key=item.event_key or f"import:{p.project_key}:{idx}:{now.timestamp()}",
                        role=item.role or "user",
                        content=str(item.content or ""),
                        source="import",
                        created_at=now,
                        occurred_at=item.occurred_at or now,
                    )
                    db.add(msg)
                    messages_created += 1
            except Exception as e:
                errors.append(f"第 {idx+1} 项: {str(e)}")
        db.commit()
    return {"memories_created": memories_created, "messages_created": messages_created,
            "total": memories_created + messages_created, "errors": errors}


@app.get("/api/admin/export/projects/{project_id}/memories")
def export_memories(project_id: int):
    """导出项目记忆为 JSON。"""
    _project_or_404(get_db_impl(), project_id)
    with session_factory() as db:
        rows = db.scalars(
            select(MemoryRow)
            .where(MemoryRow.project_id == project_id)
            .order_by(MemoryRow.updated_at.desc())
        ).all()
        return {"project_id": project_id, "memories": [
            {"id": r.id, "level": r.level, "memory_type": r.memory_type,
             "title": r.title, "content": r.content, "scope": r.scope,
             "confidence": r.confidence, "status": r.status,
             "source_kind": r.source_kind, "created_at": _safe_json(r.created_at)}
            for r in rows
        ]}


@app.get("/api/admin/export/projects/{project_id}/logs")
def export_logs(project_id: int):
    """导出项目原始日志为 JSON。"""
    _project_or_404(get_db_impl(), project_id)
    with session_factory() as db:
        rows = db.scalars(
            select(MessageRow)
            .where(MessageRow.project_id == project_id)
            .order_by(MessageRow.created_at.desc())
        ).all()
        return {"project_id": project_id, "logs": [
            {"id": r.id, "event_key": r.event_key, "role": r.role,
             "content": r.content, "source": r.source, "created_at": _safe_json(r.created_at)}
            for r in rows
        ]}



# ---------------------------------------------------------------------------
# 数据清理与归档
# ---------------------------------------------------------------------------


class CleanupRequest(BaseModel):
    project_id: int
    older_than_days: int = 30
    dry_run: bool = False


@app.post("/api/admin/cleanup/messages")
def cleanup_messages(payload: CleanupRequest):
    """清理指定时间之前的旧消息。"""
    p = _project_or_404(get_db_impl(), payload.project_id)
    from datetime import datetime, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=payload.older_than_days)
    with session_factory() as db:
        q = select(MessageRow).where(
            MessageRow.project_id == p.id,
            MessageRow.created_at < cutoff,
        )
        total = db.scalar(select(sa_func.count()).select_from(q.subquery())) or 0
        if not payload.dry_run:
            for row in db.scalars(q):
                db.delete(row)
            db.commit()
        return {"deleted": total, "dry_run": payload.dry_run, "project_id": p.id, "type": "messages"}


@app.post("/api/admin/cleanup/memories")
def cleanup_memories(payload: CleanupRequest):
    """清理指定时间之前的旧记忆。"""
    p = _project_or_404(get_db_impl(), payload.project_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=payload.older_than_days)
    with session_factory() as db:
        q = select(MemoryRow).where(
            MemoryRow.project_id == p.id,
            MemoryRow.updated_at < cutoff,
        )
        total = db.scalar(select(sa_func.count()).select_from(q.subquery())) or 0
        if not payload.dry_run:
            for row in db.scalars(q):
                db.delete(row)
            db.commit()
        return {"deleted": total, "dry_run": payload.dry_run, "project_id": p.id, "type": "memories"}


@app.post("/api/admin/cleanup/jobs")
def cleanup_jobs(payload: CleanupRequest):
    """清理指定时间之前的已完成作业。"""
    p = _project_or_404(get_db_impl(), payload.project_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=payload.older_than_days)
    with session_factory() as db:
        q = select(ProcessingJobRow).where(
            ProcessingJobRow.project_id == p.id,
            ProcessingJobRow.completed_at < cutoff,
            ProcessingJobRow.status.in_(["completed", "dead"]),
        )
        total = db.scalar(select(sa_func.count()).select_from(q.subquery())) or 0
        if not payload.dry_run:
            for row in db.scalars(q):
                db.delete(row)
            db.commit()
        return {"deleted": total, "dry_run": payload.dry_run, "project_id": p.id, "type": "jobs"}


@app.get("/api/admin/archive/projects/{project_id}")
def get_archive_status(project_id: int):
    """获取项目归档状态。"""
    from codex_memory.db_models import ArchiveStatusRow
    p = _project_or_404(get_db_impl(), project_id)
    with session_factory() as db:
        status = db.execute(
            select(ArchiveStatusRow).where(ArchiveStatusRow.project_id == p.id)
        ).scalar_one_or_none()
        if status is None:
            return {"project_id": project_id, "pending_count": 0, "dead_letter_count": 0,
                    "last_success_at": None, "last_failure_at": None}
        return _row_dict(status, "id", "project_id", "pending_count", "dead_letter_count",
                         "last_success_at", "last_failure_at", "created_at", "updated_at")



# ---------------------------------------------------------------------------
# 数据库迁移管理
# ---------------------------------------------------------------------------


@app.get("/api/admin/migrations")
def list_migrations():
    """列出所有数据库迁移状态。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext

    project_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(project_root / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)

    with _engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_rev = context.get_current_revision()

    revisions = []
    for rev in script.walk_revisions():
        revisions.append({
            "revision": rev.revision,
            "down_revision": rev.down_revision,
            "is_head": rev.revision in script.get_heads(),
            "is_current": rev.revision == current_rev,
        })

    pending = []
    if current_rev:
        for head in script.get_heads():
            for rev in script.iterate_revisions(head, current_rev):
                if rev.revision not in pending:
                    pending.append(rev.revision)
    else:
        pending = list(script.get_heads())

    return {
        "current_revision": current_rev,
        "revisions": revisions,
        "pending_revisions": pending,
        "up_to_date": len(pending) == 0,
    }


@app.post("/api/admin/migrations/upgrade")
def upgrade_migrations():
    """执行待处理的数据库迁移。"""
    from alembic.config import Config
    from alembic import command

    project_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(project_root / "alembic.ini"))
    try:
        command.upgrade(cfg, "head")
        return {"status": "ok", "message": "迁移已执行"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"迁移失败: {str(e)}")



# ---------------------------------------------------------------------------
# 系统事件（活动推送）
# ---------------------------------------------------------------------------


@app.get("/api/admin/events")
def list_events(limit: int = Query(default=50, ge=1, le=200)):
    """返回最近的系统活动事件（审计日志+作业状态+候选变化）。"""
    with session_factory() as db:
        events = []
        try:
            for row in db.scalars(
                select(SecurityAuditRow).order_by(SecurityAuditRow.id.desc()).limit(limit)
            ):
                meta = row.metadata_json or {}
                events.append({
                    "id": f"audit-{row.id}", "type": row.event_type,
                    "timestamp": _safe_json(row.created_at),
                    "summary": f"{row.event_type}: {row.subject_type}/{row.subject_id}" if row.subject_id else row.event_type,
                })
        except Exception:
            pass
        try:
            for row in db.scalars(
                select(ProcessingJobRow).order_by(ProcessingJobRow.updated_at.desc()).limit(limit)
            ):
                events.append({
                    "id": f"job-{row.id}", "type": f"job_{row.status}",
                    "timestamp": _safe_json(row.updated_at),
                    "summary": f"{row.job_type}: {row.status}",
                })
        except Exception:
            pass
        events.sort(key=lambda e: str(e.get("timestamp", "")), reverse=True)
        return {"events": events[:limit]}



# ---------------------------------------------------------------------------
# API Key 管理
# ---------------------------------------------------------------------------


class ApiKeyCreateRequest(BaseModel):
    project_id: int
    permissions: list[str] = ["read"]


@app.get("/api/admin/api-keys")
def list_api_keys():
    """列出所有 API Key。"""
    with session_factory() as db:
        rows = db.scalars(
            select(ApiKeyRow).order_by(ApiKeyRow.created_at.desc())
        ).all()
        return {"api_keys": [
            {"id": r.id, "project_id": r.project_id,
             "permissions": r.permissions, "status": r.status,
             "created_at": _safe_json(r.created_at)}
            for r in rows
        ]}


@app.post("/api/admin/api-keys")
def create_api_key(payload: ApiKeyCreateRequest):
    """创建 API Key，返回完整 token（仅本次可见）。"""
    import secrets
    _project_or_404(get_db_impl(), payload.project_id)
    raw = "cm-" + secrets.token_hex(32)
    h = hashlib.sha256(raw.encode()).hexdigest()
    with session_factory() as db:
        entry = ApiKeyRow(
            project_id=payload.project_id,
            token_hash=h,
            permissions=payload.permissions,
            status="active",
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return {"id": entry.id, "token": raw, "permissions": entry.permissions,
                "warning": "请立即保存，创建后不可再次查看"}


@app.delete("/api/admin/api-keys/{key_id}")
def delete_api_key(key_id: int):
    """吊销 API Key。"""
    with session_factory() as db:
        key = db.get(ApiKeyRow, key_id)
        if key is None:
            raise HTTPException(status_code=404, detail="API Key 不存在")
        db.delete(key)
        db.commit()
        return {"deleted": key_id}



# ---------------------------------------------------------------------------
# 审计统计与搜索
# ---------------------------------------------------------------------------


@app.get("/api/admin/audit/stats")
def audit_stats(
    project_id: int | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
):
    """审计统计：按事件类型和时间线汇总。"""
    with session_factory() as db:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        q = select(SecurityAuditRow).where(SecurityAuditRow.created_at >= since)
        if project_id is not None:
            q = q.where(SecurityAuditRow.project_id == project_id)
        rows = db.scalars(q.order_by(SecurityAuditRow.created_at.desc())).all()
        by_type, by_date = {}, {}
        for row in rows:
            by_type[row.event_type] = by_type.get(row.event_type, 0) + 1
            d = row.created_at.strftime("%Y-%m-%d")
            by_date[d] = by_date.get(d, 0) + 1
        return {
            "total": len(rows),
            "by_type": by_type,
            "by_date": [{"date": d, "count": c} for d, c in sorted(by_date.items())],
            "days": days,
        }


@app.get("/api/admin/audit/search")
def audit_search(
    q: str = Query(default="", max_length=200),
    project_id: int | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """搜索审计日志。"""
    with session_factory() as db:
        query = select(SecurityAuditRow)
        if project_id is not None:
            query = query.where(SecurityAuditRow.project_id == project_id)
        if event_type:
            query = query.where(SecurityAuditRow.event_type == event_type)
        if q:
            query = query.where(
                SecurityAuditRow.event_type.ilike(f"%{q}%")
                | SecurityAuditRow.reason_code.ilike(f"%{q}%")
                | SecurityAuditRow.subject_type.ilike(f"%{q}%")
            )
        total = db.scalar(select(sa_func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(
            query.order_by(SecurityAuditRow.id.desc()).offset(offset).limit(limit)
        ).all()
        return {
            "audit_logs": [
                _row_dict(r, "id", "project_id", "event_type", "subject_type",
                          "subject_id", "reason_code", "metadata_json", "created_at")
                for r in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }



# ---------------------------------------------------------------------------
# 系统健康检查与预警配置 (Phase 4.3)
# ---------------------------------------------------------------------------


@app.get("/api/admin/health/check")
def health_check():
    """全面系统健康检查。"""
    with session_factory() as db:
        issues = []
        db_ok = True
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
            issues.append({"severity": "critical", "type": "database", "detail": "数据库不可达"})

        if db_ok:
            stuck = db.scalar(
                select(sa_func.count(ProcessingJobRow.id))
                .where(ProcessingJobRow.status == "retry_wait")
            ) or 0
            if stuck > 0:
                issues.append({"severity": "warning" if stuck < 10 else "critical", "type": "stuck_jobs", "count": stuck})

            pending = db.scalar(
                select(sa_func.count(MemoryCandidateRow.id))
                .where(MemoryCandidateRow.status == "generated")
            ) or 0
            if pending > 0:
                issues.append({"severity": "info" if pending < 50 else "warning", "type": "pending_candidates", "count": pending})

            outbox = db.scalar(
                select(sa_func.count(OutboxEventRow.id))
                .where(OutboxEventRow.status == "pending")
            ) or 0
            if outbox > 0:
                issues.append({"severity": "warning" if outbox < 200 else "critical", "type": "outbox_backlog", "count": outbox})

        try:
            from codex_memory.maintenance import MaintenanceService
            if MaintenanceService(session_factory).is_enabled():
                issues.append({"severity": "info", "type": "maintenance_mode", "detail": "系统处于维护模式"})
        except Exception:
            pass

        return {"healthy": len(issues) == 0, "issues": issues, "total_issues": len(issues)}


class AlertConfigRequest(BaseModel):
    stuck_job_threshold: int | None = None
    candidate_threshold: int | None = None
    outbox_threshold: int | None = None
    budget_warning_pct: int | None = None


_alert_defaults = {
    "stuck_job_threshold": 10,
    "candidate_threshold": 20,
    "outbox_threshold": 50,
    "budget_warning_pct": 80,
}


@app.get("/api/admin/alerts/config")
def get_alert_config():
    """获取预警配置。"""
    return dict(_alert_defaults)


@app.put("/api/admin/alerts/config")
def update_alert_config(payload: AlertConfigRequest):
    """更新预警配置（进程生命周期内有效）。"""
    for key, value in payload.model_dump(exclude_none=True).items():
        if key in _alert_defaults:
            _alert_defaults[key] = value
    return dict(_alert_defaults)



# ---------------------------------------------------------------------------
# 管理员账户管理
# ---------------------------------------------------------------------------


@app.get("/api/admin/users")
def list_admin_users():
    """列出配置的管理员用户。"""
    return {"users": list(_admin_users.keys()),
            "total": len(_admin_users)}


@app.post("/api/admin/users")
def add_admin_user(payload: LoginRequest):
    """添加管理员用户（运行时有效）。"""
    _admin_users[payload.username] = hashlib.sha256(payload.password.encode()).hexdigest()
    return {"username": payload.username, "added": True}



# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------


@app.get("/api/admin/sessions")
def list_sessions():
    """列出活跃管理会话。"""
    return {"sessions": [{"id": k, **v} for k, v in _sessions.items()],
            "total": len(_sessions)}


@app.delete("/api/admin/sessions/{session_id}")
def revoke_session(session_id: str):
    """强制登出一个会话。"""
    if session_id in _sessions:
        del _sessions[session_id]
    return {"revoked": session_id}
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
