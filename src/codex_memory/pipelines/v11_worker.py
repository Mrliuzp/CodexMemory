from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import desc, or_, select

from sqlalchemy.orm import Session, sessionmaker

from .db_models import JobAttemptRow, OutboxEventRow, ProcessingJobRow, ProjectRow
from .idempotency import IdempotencyKeyBuilder


@dataclass(frozen=True)
class JobClaim:
    job_id: int
    job_type: str
    payload: dict
    attempt_no: int
    lease_expires_at: datetime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OutboxDispatcher:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        lease_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds
        self.now = now or _utcnow

    def dispatch_once(self, worker_id: str, limit: int = 10) -> int:
        now = self.now()
        dispatched = 0
        with self.session_factory() as session:
            query = (
                select(OutboxEventRow)
                .where(
                    OutboxEventRow.status.in_([ "pending", "retry_wait" ]),
                    OutboxEventRow.next_attempt_at <= now,
                    or_(OutboxEventRow.lease_expires_at.is_(None), OutboxEventRow.lease_expires_at <= now),
                )
                .order_by(
                    OutboxEventRow.priority.desc(),
                    OutboxEventRow.created_at,
                    OutboxEventRow.id,
                )
                .limit(limit)
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            events = session.scalars(query).all()
            for event in events:
                job_type, handler_version, idempotency_key, job_key = self._job_spec(session, event)
                if idempotency_key is not None:
                    job = session.scalar(
                        select(ProcessingJobRow).where(
                            ProcessingJobRow.project_id == event.project_id,
                            ProcessingJobRow.job_type == job_type,
                            ProcessingJobRow.idempotency_key == idempotency_key,
                        )
                    )
                else:
                    job = session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.job_key == job_key))
                if job is None:
                    session.add(
                        ProcessingJobRow(
                            project_id=event.project_id,
                            outbox_event_id=event.id,
                            job_type=job_type,
                            aggregate_type=event.aggregate_type,
                            aggregate_id=event.aggregate_id,
                            job_key=job_key,
                            source_type=event.aggregate_type,
                            source_id=str(event.aggregate_id),
                            handler_version=handler_version,
                            idempotency_key=idempotency_key,
                            payload_version=event.payload_version,
                            payload=event.payload,
                            priority=event.priority,
                        )
                    )
                event.status = "dispatched"
                event.attempt_count = (event.attempt_count or 0) + 1
                event.locked_by = worker_id
                event.locked_at = now
                event.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                event.dispatched_at = now
                dispatched += 1
            session.commit()
        return dispatched

    @staticmethod
    def _job_spec(session: Session, event: OutboxEventRow) -> tuple[str, str | None, str | None, str]:
        """Return the action contract while preserving legacy V1.1 events."""
        legacy_key = f"outbox:{event.id}:{event.event_type}:{event.aggregate_id}:{event.payload_version}"
        if not event.idempotency_key:
            return event.event_type, None, None, legacy_key

        project = session.get(ProjectRow, event.project_id)
        if project is None:
            return event.event_type, None, event.idempotency_key, event.idempotency_key
        if event.event_type == "message.appended.v1":
            operation = "extract_memory_candidate"
            handler_version = "memory-extractor-v1"
        elif event.event_type == "memory.created.v1":
            operation = "generate_embedding"
            handler_version = "embedding-v1"
        elif event.event_type == "candidate.accepted.v1":
            operation = "publish_memory"
            handler_version = "publish-v1"
        elif event.event_type == "reflection.requested.v1":
            operation = "reflect_project"
            handler_version = "reflection-v1"
        else:
            operation = event.event_type
            handler_version = event.payload_version
        key = IdempotencyKeyBuilder(project.project_key).build(
            operation,
            event.aggregate_type,
            event.aggregate_id,
            handler_version,
        )
        return operation, handler_version, key, key


class V11JobWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        lease_seconds: int = 60,
        max_backoff_seconds: int = 3600,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.now = now or _utcnow

    def claim_jobs(self, worker_id: str, limit: int = 10) -> list[JobClaim]:
        now = self.now()
        claims: list[JobClaim] = []
        with self.session_factory() as session:
            query = (
                select(ProcessingJobRow)
                .where(
                    ProcessingJobRow.status.in_([ "pending", "retry_wait" ]),
                    ProcessingJobRow.next_attempt_at <= now,
                    or_(ProcessingJobRow.lease_expires_at.is_(None), ProcessingJobRow.lease_expires_at <= now),
                )
                .order_by(
                    ProcessingJobRow.priority.desc(),
                    ProcessingJobRow.created_at,
                    ProcessingJobRow.id,
                )
                .limit(limit)
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            jobs = session.scalars(query).all()
            for job in jobs:
                job.attempt_count = (job.attempt_count or 0) + 1
                job.status = "running"
                job.locked_by = worker_id
                job.locked_at = now
                job.heartbeat_at = now
                job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                session.add(
                    JobAttemptRow(
                        job_id=job.id,
                        attempt_no=job.attempt_count,
                        worker_id=worker_id,
                        metadata_json={},
                    )
                )
                claims.append(
                    JobClaim(
                        job_id=job.id,
                        job_type=job.job_type,
                        payload=dict(job.payload or {}),
                        attempt_no=job.attempt_count,
                        lease_expires_at=job.lease_expires_at,
                    )
                )
            session.commit()
        return claims

    def heartbeat(self, job_id: int, worker_id: str) -> bool:
        now = self.now()
        with self.session_factory() as session:
            job = session.get(ProcessingJobRow, job_id)
            if job is None or job.status != "running" or job.locked_by != worker_id:
                return False
            if job.lease_expires_at is not None and job.lease_expires_at <= now:
                return False
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            session.commit()
            return True

    def complete(self, job_id: int, worker_id: str) -> bool:
        now = self.now()
        with self.session_factory() as session:
            job = session.get(ProcessingJobRow, job_id)
            if not self._owns_live_job(job, worker_id, now):
                return False
            job.status = "succeeded"
            job.completed_at = now
            job.locked_by = None
            job.locked_at = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            attempt = session.scalar(
                select(JobAttemptRow)
                .where(JobAttemptRow.job_id == job_id)
                .order_by(desc(JobAttemptRow.attempt_no))
            )
            if attempt is not None:
                attempt.ended_at = now
                attempt.outcome = "succeeded"
                attempt.finished_reason = "completed"
            self._complete_outbox(session, job, now)
            session.commit()
            return True

    def fail(
        self,
        job_id: int,
        worker_id: str,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> str | None:
        now = self.now()
        with self.session_factory() as session:
            job = session.get(ProcessingJobRow, job_id)
            if not self._owns_live_job(job, worker_id, now):
                return None
            should_retry = retryable and job.attempt_count < job.max_attempts
            job.status = "retry_wait" if should_retry else "dead"
            if should_retry:
                delay = min(self.max_backoff_seconds, 2 ** max(job.attempt_count - 1, 0))
                job.next_attempt_at = now + timedelta(seconds=delay)
            job.last_error_code = error_code
            job.last_error_message = error_message
            job.error_class = "retryable" if retryable else "permanent"
            job.locked_by = None
            job.locked_at = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            attempt = session.scalar(
                select(JobAttemptRow)
                .where(JobAttemptRow.job_id == job_id)
                .order_by(desc(JobAttemptRow.attempt_no))
            )
            if attempt is not None:
                attempt.ended_at = now
                attempt.outcome = "failed"
                attempt.error_code = error_code
                attempt.error_message = error_message
                attempt.error_class = job.error_class
                attempt.finished_reason = job.status
            self._fail_outbox(session, job, now, error_code, error_message, job.status)
            session.commit()
            return job.status

    def process_once(
        self,
        worker_id: str,
        handler: Callable[[JobClaim], None] | object,
        limit: int = 10,
    ) -> dict[str, int]:
        claims = self.claim_jobs(worker_id, limit=limit)
        completed = 0
        retry_wait = 0
        dead = 0
        from .v11_handlers import PermanentJobError

        for claim in claims:
            try:
                if hasattr(handler, "validate") and hasattr(handler, "execute"):
                    from .v13_handlers import HandlerContext

                    handler.validate(claim)  # type: ignore[attr-defined]
                    handler.execute(claim, HandlerContext())  # type: ignore[attr-defined]
                else:
                    handler(claim)  # type: ignore[operator]
            except PermanentJobError as error:
                state = self.fail(
                    claim.job_id,
                    worker_id,
                    "permanent",
                    str(error),
                    retryable=False,
                )
                if state == "dead":
                    dead += 1
                continue
            except Exception as error:
                classification = None
                if hasattr(handler, "classify_error"):
                    classification = handler.classify_error(error)  # type: ignore[attr-defined]
                state = self.fail(
                    claim.job_id,
                    worker_id,
                    classification.code if classification is not None else "handler_error",
                    str(error),
                    retryable=classification.retryable if classification is not None else True,
                )
                if state == "retry_wait":
                    retry_wait += 1
                elif state == "dead":
                    dead += 1
                continue
            if self.complete(claim.job_id, worker_id):
                completed += 1
        return {
            "claimed": len(claims),
            "completed": completed,
            "retry_wait": retry_wait,
            "dead": dead,
        }
    def sweep_expired(self) -> int:
        now = self.now()
        recovered = 0
        with self.session_factory() as session:
            jobs = session.scalars(
                select(ProcessingJobRow).where(
                    ProcessingJobRow.status == "running",
                    ProcessingJobRow.lease_expires_at.is_not(None),
                    ProcessingJobRow.lease_expires_at <= now,
                )
            ).all()
            for job in jobs:
                should_retry = job.attempt_count < job.max_attempts
                job.status = "retry_wait" if should_retry else "dead"
                if should_retry:
                    delay = min(self.max_backoff_seconds, 2 ** max(job.attempt_count - 1, 0))
                    job.next_attempt_at = now
                job.locked_by = None
                job.locked_at = None
                job.heartbeat_at = None
                job.lease_expires_at = None
                attempt = session.scalar(
                    select(JobAttemptRow)
                    .where(JobAttemptRow.job_id == job.id)
                    .order_by(desc(JobAttemptRow.attempt_no))
                )
                if attempt is not None and attempt.outcome == "running":
                    attempt.ended_at = now
                    attempt.outcome = "abandoned"
                    attempt.finished_reason = "lease_expired"
                self._fail_outbox(session, job, now, "lease_expired", "job lease expired", job.status)
                recovered += 1
            session.commit()
        return recovered

    @staticmethod
    def _owns_live_job(job: ProcessingJobRow | None, worker_id: str, now: datetime) -> bool:
        return bool(
            job is not None
            and job.status == "running"
            and job.locked_by == worker_id
            and (job.lease_expires_at is None or job.lease_expires_at > now)
        )

    @staticmethod
    def _complete_outbox(session: Session, job: ProcessingJobRow, now: datetime) -> None:
        if job.outbox_event_id is None:
            return
        event = session.get(OutboxEventRow, job.outbox_event_id)
        if event is None:
            return
        event.status = "completed"
        event.completed_at = now
        event.locked_by = None
        event.locked_at = None
        event.lease_expires_at = None

    @staticmethod
    def _fail_outbox(
        session: Session,
        job: ProcessingJobRow,
        now: datetime,
        error_code: str,
        error_message: str,
        job_status: str,
    ) -> None:
        if job.outbox_event_id is None:
            return
        event = session.get(OutboxEventRow, job.outbox_event_id)
        if event is None:
            return
        event.status = job_status if job_status in {"retry_wait", "dead"} else "retry_wait"
        event.next_attempt_at = job.next_attempt_at
        event.last_error_code = error_code
        event.last_error_message = error_message
        event.locked_by = None
        event.locked_at = None
        event.lease_expires_at = None
