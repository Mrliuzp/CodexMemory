from __future__ import annotations

from datetime import datetime, timezone

from .embedding import EmbeddingBackend, LocalTokenEmbeddingBackend
from .models import Layer, MemoryItem, RetrievalResult
from .storage import MemoryStore


LAYER_PRIORITY = {Layer.L3: 3.0, Layer.L2: 2.0, Layer.L1: 1.0}


class Retriever:
    def __init__(self, store: MemoryStore, embedding_backend: EmbeddingBackend | None = None) -> None:
        self.store = store
        self.embedding_backend = embedding_backend or LocalTokenEmbeddingBackend()

    def search(
        self,
        project_id: str,
        query: str,
        tags: list[str] | None = None,
        modules: list[str] | None = None,
        type_tags: list[str] | None = None,
        layers: list[Layer] | None = None,
        memory_types: list[str] | None = None,
        limit: int = 8,
        include_global_l2: bool = True,
    ) -> list[RetrievalResult]:
        candidates = self.store.list_memories(
            project_id,
            layers=layers,
            memory_types=memory_types,
            include_global_l2=include_global_l2,
        )
        tag_set = set(tags or [])
        tag_set.update(f"module:{module}" for module in modules or [])
        tag_set.update(f"type:{type_tag}" for type_tag in type_tags or [])
        if tag_set:
            candidates = [item for item in candidates if tag_set <= set(item.tags)]

        scored = [self._score(item, query) for item in candidates]
        scored.sort(key=lambda result: (result.priority_score, result.score), reverse=True)
        results = scored[:limit]
        self.store.increment_access([result.item.id for result in results])
        return results

    def _score(self, item: MemoryItem, query: str) -> RetrievalResult:
        searchable_text = f"{item.title}\n{item.body}\n{' '.join(item.tags)}"
        semantic = self.embedding_backend.similarity(query, searchable_text)
        recency = self._recency(item.updated_at)
        priority = LAYER_PRIORITY[item.layer]
        score = (semantic * 0.55) + (recency * 0.15) + (priority * 0.2) + (item.weight * 0.1)
        return RetrievalResult(item=item, score=score, semantic_score=semantic, recency_score=recency, priority_score=priority)

    def _recency(self, updated_at: str) -> float:
        if not updated_at:
            return 0.0
        try:
            timestamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            try:
                timestamp = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return 0.0
        age_days = max((datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).days, 0)
        return 1.0 / (1.0 + age_days)
