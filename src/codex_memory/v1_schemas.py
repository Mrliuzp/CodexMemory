from __future__ import annotations

from datetime import datetime

from typing import Any, Literal

from pydantic import BaseModel, Field


class AppendV1Request(BaseModel):
    project_key: str
    session_key: str
    event_key: str
    role: Literal["user", "assistant", "system"]
    content: str
    occurred_at: datetime | None = None
    source: str = "hook"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppendV1Response(BaseModel):
    id: int
    status: Literal["accepted", "stored", "duplicate"]
    message_id: int | None = None
    event_id: int | None = None


class MemoryV1Request(BaseModel):
    project_key: str
    level: Literal["L1"] = "L1"
    type: str
    title: str | None = None
    content: dict[str, Any]


class ContextV1Request(BaseModel):
    project_key: str
    task: str
    scope_mode: Literal["project_only", "project_and_global", "global_only"] = "project_and_global"
    layers: list[Literal["L1", "L2", "L3"]] = Field(default_factory=list)
    memory_types: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=50)
    context_budget_tokens: int = Field(default=4000, ge=1, le=12000)
    skip_pending: bool = False


class SearchV1Request(BaseModel):
    project_key: str
    query: str
    scope_mode: Literal["project_only", "project_and_global", "global_only"] = "project_and_global"
    layers: list[Literal["L1", "L2", "L3"]] = Field(default_factory=list)
    memory_types: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=50)
    include_audit: bool = False


class ReflectV1Request(BaseModel):
    project_key: str
