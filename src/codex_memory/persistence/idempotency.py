from __future__ import annotations

import re


_PART_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


def _normalise_part(value: str, field: str) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"\s+", "-", text)
    if not text or not _PART_PATTERN.fullmatch(text):
        raise ValueError(f"{field} contains invalid idempotency characters")
    return text


class IdempotencyKeyBuilder:
    """构建项目范围内稳定、可审计的业务幂等键。"""

    def __init__(self, project_key: str) -> None:
        self.project_key = _normalise_part(project_key, "project_key")

    def build(self, operation: str, source_type: str, source_id: str | int, version: str) -> str:
        parts = (
            self.project_key,
            _normalise_part(operation, "operation"),
            _normalise_part(source_type, "source_type"),
            _normalise_part(str(source_id), "source_id"),
            _normalise_part(version, "version"),
        )
        return ".".join(parts)


def build_idempotency_key(project_key: str, operation: str, source_type: str, source_id: str | int, version: str) -> str:
    return IdempotencyKeyBuilder(project_key).build(operation, source_type, source_id, version)
