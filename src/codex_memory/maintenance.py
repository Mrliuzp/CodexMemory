from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import AuditLogRow


class MaintenanceService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def set_enabled(self, enabled: bool, reason: str, actor: str) -> dict[str, Any]:
        event_type = "maintenance_mode_enabled" if enabled else "maintenance_mode_disabled"
        with self.session_factory() as session:
            log = AuditLogRow(
                project_id=None,
                event_type=event_type,
                subject_type="system",
                subject_id="maintenance",
                metadata_json={"reason": reason, "actor": actor},
            )
            session.add(log)
            session.commit()
        return {"enabled": enabled, "reason": reason}

    def is_enabled(self) -> bool:
        with self.session_factory() as session:
            latest = session.scalar(
                select(AuditLogRow)
                .where(AuditLogRow.event_type.in_([
                    "maintenance_mode_enabled",
                    "maintenance_mode_disabled",
                ]))
                .order_by(AuditLogRow.id.desc())
            )
            if latest is None:
                return False
            return latest.event_type == "maintenance_mode_enabled"

    def current_status(self) -> dict[str, Any]:
        enabled = self.is_enabled()
        if not enabled:
            return {"enabled": False, "reason": None, "actor": None}
        with self.session_factory() as session:
            latest = session.scalar(
                select(AuditLogRow)
                .where(AuditLogRow.event_type == "maintenance_mode_enabled")
                .order_by(AuditLogRow.id.desc())
            )
            meta = latest.metadata_json or {}
            return {
                "enabled": True,
                "reason": meta.get("reason"),
                "actor": meta.get("actor"),
            }
