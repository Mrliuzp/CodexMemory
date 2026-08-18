"""候选自动发布策略的可替换持久化适配器。

当前基线没有 V1.6 决策策略表，因此使用已有项目处理策略 JSON 保存一个
命名空间；检测到 48fb 提供的 ``project_decision_policies`` 后自动切换到
其 ``DecisionService``。这样阈值可以由 Admin 显式配置，又不会在本任务中
复制另一块持久化契约。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from .db_models import ProjectProcessingPolicyRow, ProjectRow, SecurityAuditRow
from .v11_decision import resolve_l1_auto_publish_threshold


DEFAULT_POLICY_VERSION = "decision-policy-v1"


@dataclass(frozen=True)
class CandidateDecisionPolicy:
    """服务器自动路径所需的最小策略视图。"""

    enabled: bool = False
    auto_publish_enabled: bool = False
    strategy: str = "manual_review"
    allowed_level: str = "L1"
    allowed_scope: str = "project"
    min_confidence: float = 0.80
    require_evidence: bool = True
    allow_risk_flags: bool = False
    policy_version: str = DEFAULT_POLICY_VERSION
    updated_by: str = "system"
    source: str = "v16_policy_default"

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto_publish_enabled": self.auto_publish_enabled,
            "strategy": self.strategy,
            "allowed_level": self.allowed_level,
            "allowed_scope": self.allowed_scope,
            "min_confidence": self.min_confidence,
            "require_evidence": self.require_evidence,
            "allow_risk_flags": self.allow_risk_flags,
            "policy_version": self.policy_version,
            "updated_by": self.updated_by,
            "source": self.source,
        }


class DecisionPolicyProvider(Protocol):
    """Worker/API 可注入的项目级策略读取与更新边界。"""

    def get(self, project_id: int) -> CandidateDecisionPolicy:
        ...

    def update(self, project_id: int, **changes: Any) -> CandidateDecisionPolicy:
        ...


def _threshold(value: Any) -> float:
    if value is None:
        return resolve_l1_auto_publish_threshold()
    return resolve_l1_auto_publish_threshold(float(value))


class SqlDecisionPolicyProvider:
    """优先适配 48fb V1.6 策略表，旧基线使用已有 JSON 命名空间。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get(self, project_id: int) -> CandidateDecisionPolicy:
        with self.session_factory() as session:
            model = self._v16_model(session)
            if model is not None:
                row = session.get(model, project_id)
                if row is None:
                    return self._v16_default()
                return self._from_row(row, source="v16_policy")
            return self._legacy_policy(session, project_id)

    def update(self, project_id: int, **changes: Any) -> CandidateDecisionPolicy:
        self._validate_changes(changes)
        with self.session_factory() as session:
            model = self._v16_model(session)
        if model is not None:
            try:
                from ..decision_service import DecisionService
            except ImportError as error:
                raise RuntimeError("V1.6 决策策略适配器尚未安装") from error
            DecisionService(self.session_factory).update_policy(project_id, **changes)
            return self.get(project_id)

        with self.session_factory() as session:
            project = session.get(ProjectRow, project_id)
            if project is None:
                raise LookupError(f"project does not exist: {project_id}")
            row = session.get(ProjectProcessingPolicyRow, project_id)
            if row is None:
                row = ProjectProcessingPolicyRow(project_id=project_id)
                session.add(row)
                session.flush()
            payload = dict(row.data_residency_policy or {})
            current = dict(payload.get("decision_policy") or self._legacy_policy_values())
            current.update(changes)
            policy = self._policy_from_values(current, source="legacy_fallback")
            payload["decision_policy"] = policy.as_dict()
            row.data_residency_policy = payload
            session.add(
                SecurityAuditRow(
                    project_id=project_id,
                    event_type="decision_policy_updated",
                    subject_type="decision_policy",
                    subject_id=str(project_id),
                    reason_code="admin_update",
                    metadata_json={"changes": sorted(changes), "policy": policy.as_dict()},
                )
            )
            session.commit()
            return policy

    @staticmethod
    def _validate_changes(changes: Mapping[str, Any]) -> None:
        allowed = {"enabled", "auto_publish_enabled", "strategy", "min_confidence", "updated_by"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"未知决策策略字段：{sorted(unknown)[0]}")
        if not changes:
            raise ValueError("至少需要一个决策策略字段")
        if "min_confidence" in changes:
            if changes["min_confidence"] is None:
                raise ValueError("min_confidence 不能为空")
            _threshold(changes["min_confidence"])
        for name in ("enabled", "auto_publish_enabled"):
            if name in changes and not isinstance(changes[name], bool):
                raise ValueError(f"{name} 必须是布尔值")
        if "strategy" in changes and changes["strategy"] not in {"manual_review", "auto_publish"}:
            raise ValueError("strategy 必须是 manual_review 或 auto_publish")
        if "updated_by" in changes and (not isinstance(changes["updated_by"], str) or not changes["updated_by"].strip()):
            raise ValueError("updated_by 不能为空")

    @staticmethod
    def _v16_model(session: Session) -> Any | None:
        try:
            from ..persistence.v16_models import ProjectDecisionPolicyRow
        except ImportError:
            return None
        if session.bind is None or not inspect(session.bind).has_table(ProjectDecisionPolicyRow.__tablename__):
            return None
        return ProjectDecisionPolicyRow

    @staticmethod
    def _v16_default() -> CandidateDecisionPolicy:
        return CandidateDecisionPolicy(
            enabled=False,
            auto_publish_enabled=False,
            strategy="manual_review",
            min_confidence=0.80,
            source="v16_policy_default",
        )

    @staticmethod
    def _from_row(row: Any, *, source: str) -> CandidateDecisionPolicy:
        try:
            confidence = _threshold(getattr(row, "min_confidence", None))
        except ValueError:
            return CandidateDecisionPolicy(
                enabled=False,
                auto_publish_enabled=False,
                strategy="manual_review",
                source=f"{source}_invalid",
            )
        return CandidateDecisionPolicy(
            enabled=bool(getattr(row, "enabled", False)),
            auto_publish_enabled=bool(getattr(row, "auto_publish_enabled", False)),
            strategy=str(getattr(row, "strategy", "manual_review")),
            allowed_level=str(getattr(row, "allowed_level", "L1")),
            allowed_scope=str(getattr(row, "allowed_scope", "project")),
            min_confidence=confidence,
            require_evidence=bool(getattr(row, "require_evidence", True)),
            allow_risk_flags=bool(getattr(row, "allow_risk_flags", False)),
            policy_version=str(getattr(row, "policy_version", DEFAULT_POLICY_VERSION)),
            updated_by=str(getattr(row, "updated_by", "system")),
            source=source,
        )

    @classmethod
    def _legacy_policy(cls, session: Session, project_id: int) -> CandidateDecisionPolicy:
        row = session.get(ProjectProcessingPolicyRow, project_id)
        values = cls._legacy_policy_values()
        if row is not None:
            stored = dict(row.data_residency_policy or {}).get("decision_policy")
            if isinstance(stored, Mapping):
                values.update(stored)
        return cls._policy_from_values(values, source="legacy_fallback")

    @staticmethod
    def _legacy_policy_values() -> dict[str, Any]:
        try:
            threshold = resolve_l1_auto_publish_threshold()
        except ValueError:
            threshold = 0.80
        return {
            "enabled": False,
            "auto_publish_enabled": False,
            "strategy": "manual_review",
            "allowed_level": "L1",
            "allowed_scope": "project",
            "min_confidence": threshold,
            "require_evidence": True,
            "allow_risk_flags": False,
            "policy_version": DEFAULT_POLICY_VERSION,
            "updated_by": "system",
        }

    @classmethod
    def _policy_from_values(cls, values: Mapping[str, Any], *, source: str) -> CandidateDecisionPolicy:
        try:
            threshold = _threshold(values.get("min_confidence"))
        except (TypeError, ValueError):
            return CandidateDecisionPolicy(enabled=False, auto_publish_enabled=False, strategy="manual_review", source=f"{source}_invalid")
        allowed_level = str(values.get("allowed_level", "L1"))
        allowed_scope = str(values.get("allowed_scope", "project"))
        if allowed_level != "L1" or allowed_scope != "project":
            return CandidateDecisionPolicy(enabled=False, auto_publish_enabled=False, strategy="manual_review", min_confidence=threshold, source=f"{source}_unsafe")
        return CandidateDecisionPolicy(
            enabled=bool(values.get("enabled", False)),
            auto_publish_enabled=bool(values.get("auto_publish_enabled", False)),
            strategy=str(values.get("strategy", "manual_review")),
            allowed_level=allowed_level,
            allowed_scope=allowed_scope,
            min_confidence=threshold,
            require_evidence=bool(values.get("require_evidence", True)),
            allow_risk_flags=False,
            policy_version=str(values.get("policy_version", DEFAULT_POLICY_VERSION)),
            updated_by=str(values.get("updated_by", "system")),
            source=source,
        )


__all__ = ["CandidateDecisionPolicy", "DecisionPolicyProvider", "SqlDecisionPolicyProvider"]
