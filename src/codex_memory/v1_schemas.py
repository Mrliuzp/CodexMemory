from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AppendV1Request(BaseModel):
    project_key: str
    session_key: str
    event_key: str
    role: Literal["user", "assistant", "system"]
    content: str
    source: str = "hook"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppendV1Response(BaseModel):
    id: int
    status: Literal["stored", "duplicate"]


class MemoryV1Request(BaseModel):
    project_key: str
    level: Literal["L1"] = "L1"
    type: str
    title: str | None = None
    content: dict[str, Any]


class ContextV1Request(BaseModel):
    project_key: str
    task: str
    limit: int = Field(default=8, ge=1, le=50)


class SearchV1Request(BaseModel):
    project_key: str
    query: str
    limit: int = Field(default=8, ge=1, le=50)


class ReflectV1Request(BaseModel):
    project_key: str
