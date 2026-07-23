from __future__ import annotations

from collections import defaultdict

from .embedding import EmbeddingBackend, LocalTokenEmbeddingBackend
from .models import Layer, MemoryItem
from .storage import MemoryStore


class ReflectionEngine:
    def __init__(self, store: MemoryStore, embedding_backend: EmbeddingBackend | None = None) -> None:
        self.store = store
        self.embedding_backend = embedding_backend or LocalTokenEmbeddingBackend()

    def run(self, project_id: str, promote_threshold: int = 3) -> dict[str, int]:
        promoted = self.promote_frequent_l1(project_id, promote_threshold=promote_threshold)
        merged = self.cluster_similar(project_id)
        synthesized = self.synthesize_stable_rules(project_id)
        decayed = self.store.decay_stale_l1(project_id=project_id)
        deleted = self.store.delete_low_value_l1(project_id=project_id)
        metrics = {
            "promoted": promoted,
            "synthesized": synthesized,
            "merged": merged,
            "decayed": decayed,
            "deleted": deleted,
        }
        summary = self.generate_summary(project_id, metrics)
        report_id = self.store.add_reflection_report(project_id, summary, metrics)
        return {**metrics, "report_id": report_id}

    def promote_frequent_l1(self, project_id: str, promote_threshold: int = 3) -> int:
        promoted = 0
        for item in self.store.list_memories(project_id, layers=[Layer.L1], include_global_l2=False):
            if item.access_count >= promote_threshold:
                self.store.upsert_memory(
                    project_id=project_id,
                    layer=Layer.L2,
                    title=item.title.replace("Working:", "Knowledge:", 1),
                    body=item.body,
                    tags=sorted(set(item.tags + ["promoted"])),
                    memory_type="knowledge",
                    source_log_ids=item.source_log_ids,
                    metadata={**item.metadata, "promoted_from": item.id},
                    weight=max(item.weight, 2.0),
                )
                promoted += 1
        return promoted

    def synthesize_stable_rules(self, project_id: str, min_sources: int = 2) -> int:
        synthesized = 0
        for item in self.store.list_memories(project_id, layers=[Layer.L1], include_global_l2=False):
            if item.memory_type == "conversation":
                continue
            if len(item.source_log_ids) < min_sources and item.version < min_sources:
                continue
            title = item.title
            for prefix in ["Working:", "Problem:", "Conversation:"]:
                title = title.replace(prefix, "Knowledge:", 1)
            if not title.startswith("Knowledge:"):
                title = f"Knowledge: {title}"
            self.store.upsert_memory(
                project_id=project_id,
                layer=Layer.L2,
                title=title,
                body=item.body,
                tags=sorted(set(item.tags + ["synthesized", "stable-rule"])),
                memory_type="knowledge",
                source_log_ids=item.source_log_ids,
                metadata={**item.metadata, "synthesized_from": item.id},
                weight=max(item.weight, 2.0),
            )
            synthesized += 1
        return synthesized

    def cluster_similar(self, project_id: str, threshold: float = 0.92) -> int:
        items = [
            item
            for item in self.store.list_memories(project_id, include_global_l2=False)
            if item.layer != Layer.L3
        ]
        groups: dict[int, list[MemoryItem]] = defaultdict(list)
        assigned: set[int] = set()
        for item in items:
            if item.id in assigned:
                continue
            groups[item.id].append(item)
            assigned.add(item.id)
            for other in items:
                if other.id in assigned or item.layer != other.layer or item.memory_type != other.memory_type:
                    continue
                if self.embedding_backend.similarity(item.body, other.body) >= threshold:
                    groups[item.id].append(other)
                    assigned.add(other.id)

        merged = 0
        duplicate_ids: list[int] = []
        for group in groups.values():
            if len(group) < 2:
                continue
            primary = group[0]
            for duplicate in group[1:]:
                self.store.upsert_memory(
                    project_id=primary.project_id,
                    layer=primary.layer,
                    title=primary.title,
                    body=duplicate.body,
                    tags=duplicate.tags,
                    memory_type=primary.memory_type,
                    source_log_ids=duplicate.source_log_ids,
                    metadata={**primary.metadata, "merged_duplicate": duplicate.id},
                    weight=max(primary.weight, duplicate.weight),
                )
                duplicate_ids.append(duplicate.id)
                merged += 1
        self.store.delete_memories(duplicate_ids, allowed_layers=[Layer.L1, Layer.L2])
        return merged

    def generate_summary(self, project_id: str, metrics: dict[str, int]) -> str:
        memories = self.store.list_memories(project_id, include_global_l2=False)
        by_layer = {Layer.L1: 0, Layer.L2: 0, Layer.L3: 0}
        top_errors: list[str] = []
        top_knowledge: list[str] = []
        for item in memories:
            by_layer[item.layer] += 1
            if item.layer == Layer.L3 and len(top_errors) < 5:
                top_errors.append(item.title)
            if item.layer == Layer.L2 and len(top_knowledge) < 5:
                top_knowledge.append(item.title)

        lines = [
            f"Reflection summary for project {project_id}",
            f"L1 working memories: {by_layer[Layer.L1]}",
            f"L2 knowledge items: {by_layer[Layer.L2]}",
            f"L3 error memories: {by_layer[Layer.L3]}",
            f"Promoted L1 to L2: {metrics['promoted']}",
            f"Synthesized stable rules: {metrics['synthesized']}",
            f"Merged similar memories: {metrics['merged']}",
            f"Decayed stale L1: {metrics['decayed']}",
            f"Deleted low-value L1: {metrics['deleted']}",
        ]
        if top_errors:
            lines.append("Important error memories:")
            lines.extend(f"- {title}" for title in top_errors)
        if top_knowledge:
            lines.append("Stable knowledge:")
            lines.extend(f"- {title}" for title in top_knowledge)
        return "\n".join(lines)
