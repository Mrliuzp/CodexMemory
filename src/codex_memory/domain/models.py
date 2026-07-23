from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Layer(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


@dataclass(frozen=True)
class RawLog:
    id: int
    project_id: str
    conversation_id: str
    role: str
    content: str
    metadata: dict[str, Any]
    created_at: str
    processed_at: str | None


@dataclass(frozen=True)
class MemoryItem:
    id: int
    project_id: str | None
    layer: Layer
    title: str
    body: str
    tags: list[str]
    memory_type: str
    source_log_ids: list[int]
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    weight: float = 1.0
    access_count: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class RetrievalResult:
    item: MemoryItem
    score: float
    semantic_score: float
    recency_score: float
    priority_score: float
