from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .models import Layer
from .service import MemoryService


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str
    metadata: dict[str, Any] | None = None


class CodexMemoryRuntime:
    """\u6355\u83b7\u6bcf\u8f6e\u5bf9\u8bdd\uff0c\u5e76\u5728\u56de\u7b54\u524d\u6784\u5efa\u6ce8\u5165\u4e0a\u4e0b\u6587\u7684\u8fd0\u884c\u65f6\u8fb9\u754c\u3002"""

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    def record_conversation(
        self,
        project_id: str,
        conversation_id: str,
        messages: Iterable[ConversationMessage],
        process_now: bool = False,
        enqueue_async: bool = False,
    ) -> list[int]:
        raw_ids: list[int] = []
        for message in messages:
            raw_ids.append(
                self.service.append_conversation(
                    project_id=project_id,
                    conversation_id=conversation_id,
                    role=message.role,
                    content=message.content,
                    metadata=message.metadata,
                    process_now=process_now,
                    enqueue_async=enqueue_async,
                )
            )
        return raw_ids

    def prepare_answer_context(
        self,
        project_id: str,
        current_task: str,
        project_context: str | None = None,
        tags: list[str] | None = None,
        modules: list[str] | None = None,
        type_tags: list[str] | None = None,
        layers: list[Layer] | None = None,
        memory_types: list[str] | None = None,
        process_pending: bool = True,
    ) -> str:
        if process_pending:
            self.service.process_project_pending_memories(project_id)
        return self.service.build_context(
            project_id=project_id,
            current_task=current_task,
            project_context=project_context,
            tags=tags,
            modules=modules,
            type_tags=type_tags,
            layers=layers,
            memory_types=memory_types,
        )
