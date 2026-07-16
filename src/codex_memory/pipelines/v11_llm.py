from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    MemoryCandidateRow,
    MessageRow,
    ProjectFeatureFlagRow,
    ProjectProcessingPolicyRow,
    ProjectRow,
)


_REQUIRED_FIELDS = (
    "error",
    "context",
    "trigger_condition",
    "root_cause",
    "fix",
    "anti_pattern",
)


class ErrorMemoryExtractor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider: Callable[[str], dict[str, Any]],
        *,
        model: str = "shadow-provider",
        prompt_version: str = "error-memory-v1",
    ) -> None:
        self.session_factory = session_factory
        self.provider = provider
        self.model = model
        self.prompt_version = prompt_version

    def extract(self, project_id: int, message_id: int) -> MemoryCandidateRow:
        with self.session_factory() as session:
            project = session.get(ProjectRow, project_id)
            message = session.get(MessageRow, message_id)
            if project is None or message is None or message.project_id != project_id:
                raise ValueError("message does not belong to project")
            flags = session.get(ProjectFeatureFlagRow, project_id)
            policy = session.get(ProjectProcessingPolicyRow, project_id)
            raw_content = message.content
            injection = self._looks_like_prompt_injection(raw_content)
            enabled = flags is not None and flags.llm_shadow_enabled
            remote_allowed = policy is not None and policy.remote_llm_allowed
            result: dict[str, Any] = {}
            abstain = not enabled or not remote_allowed or injection
            if not abstain:
                prompt = self._build_prompt(self._redact(raw_content))
                result = self.provider(prompt)
                if not isinstance(result, dict) or not all(
                    isinstance(result.get(field), str) and result[field].strip() for field in _REQUIRED_FIELDS
                ):
                    abstain = True
            content = {field: str(result.get(field, "")) for field in _REQUIRED_FIELDS}
            content["confidence"] = max(0.0, min(float(result.get("confidence", 0.0) or 0.0), 1.0))
            candidate = MemoryCandidateRow(
                project_id=project_id,
                source_message_id=message_id,
                task_type="error_memory",
                level="L3",
                scope="project",
                memory_type="error_memory",
                title=content["error"] or "Shadow error memory",
                content=content,
                model=self.model,
                prompt_version=self.prompt_version,
                classifier_version="llm-shadow-v1",
                model_confidence=content["confidence"],
                status="shadow",
                abstain=abstain,
            )
            session.add(candidate)
            session.commit()
            return candidate

    @staticmethod
    def _redact(text: str) -> str:
        return re.sub(r"(?i)(sk-[A-Za-z0-9_-]+|Bearer[ ]+[^ ]+)", "[REDACTED]", text)

    @staticmethod
    def _looks_like_prompt_injection(text: str) -> bool:
        lowered = text.lower()
        return "ignore previous instructions" in lowered or "system prompt" in lowered

    @staticmethod
    def _build_prompt(content: str) -> str:
        return (
            "Extract an error memory from the immutable source message. "
            "Return only the required JSON fields."
            + chr(10)
            + "Source message:"
            + chr(10)
            + content
        )