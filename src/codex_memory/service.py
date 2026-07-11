from __future__ import annotations

from pathlib import Path
from typing import Any

from .context import ContextBuilder
from .embedding import EmbeddingBackend
from .models import Layer, RetrievalResult
from .processor import LayeringProcessor
from .reflection import ReflectionEngine
from .retrieval import Retriever
from .storage import MemoryStore


class MemoryService:
    def __init__(self, db_path: str | Path = "memory.db", embedding_backend: EmbeddingBackend | None = None) -> None:
        self.store = MemoryStore(db_path)
        self.processor = LayeringProcessor(self.store)
        self.retriever = Retriever(self.store, embedding_backend=embedding_backend)
        self.context_builder = ContextBuilder()
        self.reflection = ReflectionEngine(self.store, embedding_backend=embedding_backend)

    def append_conversation(
        self,
        project_id: str,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        process_now: bool = False,
        enqueue_async: bool = False,
    ) -> int:
        raw_id = self.store.append_raw_log(project_id, conversation_id, role, content, metadata)
        if process_now:
            self.processor.process_project(project_id)
        elif enqueue_async:
            self.processor.start()
            self.processor.enqueue(project_id)
        return raw_id

    def retrieve(
        self,
        project_id: str,
        query: str,
        tags: list[str] | None = None,
        modules: list[str] | None = None,
        type_tags: list[str] | None = None,
        layers: list[Layer] | None = None,
        memory_types: list[str] | None = None,
        limit: int = 8,
    ) -> list[RetrievalResult]:
        return self.retriever.search(
            project_id,
            query,
            tags=tags,
            modules=modules,
            type_tags=type_tags,
            layers=layers,
            memory_types=memory_types,
            limit=limit,
        )

    def build_context(
        self,
        project_id: str,
        current_task: str,
        tags: list[str] | None = None,
        modules: list[str] | None = None,
        type_tags: list[str] | None = None,
        layers: list[Layer] | None = None,
        memory_types: list[str] | None = None,
        limit: int = 8,
        project_context: str | None = None,
    ) -> str:
        results = self.retrieve(
            project_id,
            current_task,
            tags=tags,
            modules=modules,
            type_tags=type_tags,
            layers=layers,
            memory_types=memory_types,
            limit=limit,
        )
        return self.context_builder.build(project_id, current_task, results, project_context=project_context)

    def start_async_processor(self) -> None:
        self.processor.start()

    def stop_async_processor(self) -> None:
        self.processor.stop()

    def drain_async_processor(self) -> None:
        self.processor.drain()

    def run_reflection(self, project_id: str) -> dict[str, int]:
        return self.reflection.run(project_id)

    def list_reflection_reports(self, project_id: str) -> list[dict[str, Any]]:
        return self.store.list_reflection_reports(project_id)

    def list_raw_logs(self, project_id: str) -> list[dict[str, Any]]:
        return [raw.__dict__ for raw in self.store.list_raw_logs(project_id=project_id)]

    def list_processing_jobs(self, project_id: str) -> list[dict[str, Any]]:
        return self.store.list_jobs(project_id)

    def process_pending_memories(self) -> int:
        return self.processor.process_pending()

    def process_project_pending_memories(self, project_id: str) -> int:
        return self.processor.process_project(project_id)

    def retry_failed_layering_jobs(self, project_id: str) -> int:
        return self.processor.retry_failed(project_id)

    def reset_stale_running_layering_jobs(self, project_id: str, older_than_minutes: int = 30) -> int:
        return self.processor.reset_stale_running(project_id, older_than_minutes)

    def rebuild_project_from_l0(self, project_id: str) -> dict[str, int]:
        report = self.processor.rebuild_project_from_l0(project_id)
        event_id = self.store.add_governance_event(
            project_id=project_id,
            event_type="rebuild_project_from_l0",
            subject_id=None,
            reviewer="system",
            reason="rebuild derived memories from L0 source of truth",
            metadata=report,
        )
        return {**report, "governance_event_id": event_id}

    def health_status(self) -> dict[str, Any]:
        return self.store.health_status()

    def promote_to_global_l2(
        self,
        project_id: str,
        memory_id: int,
        reviewer: str,
        reason: str,
    ) -> dict[str, Any]:
        memory = self.store.get_memory(memory_id)
        if memory is None:
            raise ValueError(f"memory not found: {memory_id}")
        if memory.project_id != project_id:
            raise ValueError("memory does not belong to the requested project")
        if memory.layer != Layer.L2:
            raise ValueError("only project L2 knowledge can be promoted to global L2")

        global_id = self.store.upsert_memory(
            project_id=None,
            layer=Layer.L2,
            title=memory.title,
            body=memory.body,
            tags=sorted(set(memory.tags + ["global"])),
            memory_type=memory.memory_type,
            source_log_ids=memory.source_log_ids,
            metadata={**memory.metadata, "global_from_project": project_id, "global_from_memory_id": memory.id},
            weight=max(memory.weight, 2.5),
        )
        event_id = self.store.add_governance_event(
            project_id=project_id,
            event_type="promote_global_l2",
            subject_id=memory.id,
            reviewer=reviewer,
            reason=reason,
            metadata={"global_memory_id": global_id},
        )
        return {"global_memory_id": global_id, "governance_event_id": event_id}

    def export_project_audit(self, project_id: str) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "raw_logs": [raw.__dict__ for raw in self.store.list_raw_logs(project_id=project_id)],
            "memories": [
                {
                    "id": memory.id,
                    "project_id": memory.project_id,
                    "layer": memory.layer.value,
                    "title": memory.title,
                    "body": memory.body,
                    "tags": memory.tags,
                    "memory_type": memory.memory_type,
                    "source_log_ids": memory.source_log_ids,
                    "metadata": memory.metadata,
                    "version": memory.version,
                    "weight": memory.weight,
                    "access_count": memory.access_count,
                    "created_at": memory.created_at,
                    "updated_at": memory.updated_at,
                }
                for memory in self.store.list_memories(project_id, include_global_l2=False)
            ],
            "memory_versions": self.store.list_memory_versions(project_id),
            "processing_jobs": self.store.list_jobs(project_id),
            "reflection_reports": self.store.list_reflection_reports(project_id),
            "governance_events": self.store.list_governance_events(project_id),
        }
