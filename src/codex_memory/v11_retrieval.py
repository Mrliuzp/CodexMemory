from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    EmbeddingProfileRow,
    MemoryEmbeddingVectorRow,
    MemoryRow,
    ProjectFeatureFlagRow,
    ProjectRetrievalProfileRow,
    ProjectRow,
    RetrievalAuditRow,
)


_RRF_K = 60
_LAYER_ORDER = {"L3": 0, "L2": 1, "L1": 2}
_VALID_SCOPE_MODES = {"project_only", "project_and_global", "global_only"}


class V11Retriever:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def search(
        self,
        project_key: str,
        query: str,
        *,
        scope_mode: str = "project_and_global",
        layers: list[str] | None = None,
        memory_types: list[str] | None = None,
        limit: int = 8,
        include_audit: bool = False,
    ) -> dict[str, Any]:
        if scope_mode not in _VALID_SCOPE_MODES:
            raise ValueError(f"unsupported scope_mode: {scope_mode}")
        limit = max(1, min(limit, 50))
        query_tokens = self._tokens(query)
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"project does not exist: {project_key}")
            rows = session.scalars(
                select(MemoryRow)
                .where(
                    MemoryRow.status.in_(["published", "accepted", "active"]),
                    MemoryRow.review_status.in_(["published", "accepted"]),
                    MemoryRow.deprecated.is_(False),
                )
                .order_by(MemoryRow.id)
            ).all()
            rows = [
                row
                for row in rows
                if self._scope_matches(row, project.id, scope_mode)
                and (not layers or row.level in set(layers))
                and (not memory_types or row.memory_type in set(memory_types))
            ]
            flags = session.get(ProjectFeatureFlagRow, project.id)
            retrieval_profile = session.get(ProjectRetrievalProfileRow, project.id)
            dense_enabled = bool(flags is not None and flags.dense_retrieval_enabled)
            profile_id = retrieval_profile.active_embedding_profile_id if retrieval_profile is not None else None
            degraded = False
            degraded_reason = None
            profile = session.get(EmbeddingProfileRow, profile_id) if profile_id is not None else None
            if dense_enabled and profile is None:
                degraded = True
                degraded_reason = "active_profile_missing"
            scored: list[tuple[MemoryRow, int]] = []
            for row in rows:
                score = self._lexical_score(query_tokens, self._memory_text(row))
                if score > 0 or not query_tokens or dense_enabled:
                    scored.append((row, score))
            lexical_sorted = sorted(
                scored,
                key=lambda item: (
                    -item[1],
                    _LAYER_ORDER.get(item[0].level, 99),
                    item[0].id,
                )
            )
            lexical_rank = {row.id: index for index, (row, _) in enumerate(lexical_sorted, start=1)}
            dense_rank: dict[int, int] = {}
            dense_score: dict[int, float] = {}
            if dense_enabled and profile is not None:
                query_vector = self._profile_vector(query, profile)
                vector_rows = session.scalars(
                    select(MemoryEmbeddingVectorRow).where(
                        MemoryEmbeddingVectorRow.embedding_profile_id == profile.id,
                        MemoryEmbeddingVectorRow.memory_id.in_([row.id for row, _ in scored]),
                    )
                ).all()
                for vector_row in vector_rows:
                    score = self._dot(query_vector, list(vector_row.embedding or []))
                    dense_score[vector_row.memory_id] = max(dense_score.get(vector_row.memory_id, -1.0), score)
                if not dense_score:
                    degraded = True
                    degraded_reason = "dense_vectors_missing"
                else:
                    dense_rank = {
                        memory_id: index
                        for index, (memory_id, _) in enumerate(
                            sorted(dense_score.items(), key=lambda item: (-item[1], item[0])),
                            start=1,
                        )
                    }
            if dense_enabled and profile is not None and dense_rank and not degraded:
                retrieval_mode = "hybrid"
                ordered = sorted(
                    scored,
                    key=lambda item: (
                        -(
                            (1.0 / (_RRF_K + lexical_rank.get(item[0].id, len(scored) + 1)))
                            + (1.0 / (_RRF_K + dense_rank.get(item[0].id, len(scored) + 1)))
                        ),
                        _LAYER_ORDER.get(item[0].level, 99),
                        item[0].id,
                    ),
                )
                results = []
                for index, (row, score) in enumerate(ordered[:limit], start=1):
                    rrf_score = (1.0 / (_RRF_K + lexical_rank.get(row.id, len(scored) + 1))) + (
                        1.0 / (_RRF_K + dense_rank.get(row.id, len(scored) + 1))
                    )
                    payload = self._result_payload(row, index, score)
                    payload["rrf_score"] = rrf_score
                    results.append(payload)
            else:
                retrieval_mode = "lexical"
                results = [
                    self._result_payload(row, rank=index, lexical_score=score)
                    for index, (row, score) in enumerate(lexical_sorted[:limit], start=1)
                ]
            response: dict[str, Any] = {
                "retrieval_mode": retrieval_mode,
                "degraded": degraded,
                "degraded_reason": degraded_reason,
                "profile_id": profile_id if dense_enabled else None,
                "parameters": {
                    "scope_mode": scope_mode,
                    "layers": layers or [],
                    "memory_types": memory_types or [],
                    "limit": limit,
                    "rrf_k": _RRF_K,
                    "dense_enabled": dense_enabled,
                },
                "results": results,
            }
            if include_audit:
                audit = RetrievalAuditRow(
                    project_id=project.id,
                    query_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
                    retrieval_mode=retrieval_mode,
                    degraded=degraded,
                    degraded_reason=degraded_reason,
                    profile_id=profile_id if dense_enabled else None,
                    parameters=response["parameters"],
                    result_ids=[item["memory_id"] for item in results],
                )
                session.add(audit)
                session.flush()
                response["audit_id"] = audit.id
            session.commit()
            return response

    def build_context(
        self,
        project_key: str,
        task: str,
        *,
        scope_mode: str = "project_and_global",
        layers: list[str] | None = None,
        memory_types: list[str] | None = None,
        limit: int = 8,
        context_budget_tokens: int = 4000,
    ) -> dict[str, Any]:
        budget = max(1, min(context_budget_tokens, 12000))
        search = self.search(
            project_key,
            task,
            scope_mode=scope_mode,
            layers=layers,
            memory_types=memory_types,
            limit=limit,
        )
        grouped: dict[str, list[dict[str, Any]]] = {"L3": [], "L2": [], "L1": []}
        for item in search["results"]:
            grouped.setdefault(item["level"], []).append(item)
        newline = chr(10)
        prefix = (
            f"[Project Context]{newline}"
            f"project_id: {project_key}{newline}{newline}"
            f"[Error Memory - L3]{newline}"
        )
        text = prefix
        source_ids: list[int] = []
        remaining = [item for level in ("L3", "L2", "L1") for item in grouped.get(level, [])]
        truncated = False
        for level, heading in (
            ("L3", "[Knowledge Errors - L3]"),
            ("L2", "[Knowledge Base - L2]"),
            ("L1", "[Working Memory - L1]"),
        ):
            if level != "L3":
                text += f"{newline}{newline}{heading}{newline}"
            for item in grouped.get(level, []):
                line = (
                    f"- {item['title']} (type={item['memory_type']}){newline}"
                    f"  {item['content']}{newline}"
                )
                candidate = text + line + f"{newline}[Current Task]{newline}{task}"
                if self._estimate_tokens(candidate) > budget:
                    truncated = True
                    continue
                text += line
                source_ids.append(item["memory_id"])
        text += f"{newline}[Current Task]{newline}{task}"
        used = min(self._estimate_tokens(text), budget)
        if len(source_ids) < len(remaining):
            truncated = True
        return {
            "context": text,
            "source_ids": source_ids,
            "retrieval": {
                key: search[key]
                for key in ("retrieval_mode", "degraded", "degraded_reason", "profile_id", "parameters")
            },
            "budget": {"limit_tokens": budget, "used_tokens": used, "truncated": truncated},
        }

    @staticmethod
    def _profile_vector(query: str, profile: EmbeddingProfileRow) -> list[float]:
        from .v11_embedding import EmbeddingProfileService

        return EmbeddingProfileService._vector(query, profile.dimension, profile.normalization)

    @staticmethod
    def _dot(left: list[float], right: list[float]) -> float:
        if len(left) != len(right) or not left:
            return -1.0
        return sum(a * b for a, b in zip(left, right))
    @staticmethod
    def _scope_matches(row: MemoryRow, project_id: int, scope_mode: str) -> bool:
        if scope_mode == "project_only":
            return row.project_id == project_id and row.scope == "project"
        if scope_mode == "global_only":
            return row.project_id is None and row.scope == "global"
        return (row.project_id == project_id and row.scope == "project") or (
            row.project_id is None and row.scope == "global"
        )

    @staticmethod
    def _memory_text(row: MemoryRow) -> str:
        content = json.dumps(row.content or {}, ensure_ascii=False, sort_keys=True)
        return f"{row.title or ''} {content}".lower()

    @classmethod
    def _tokens(cls, text: str) -> set[str]:
        lowered = text.lower()
        tokens = set(re.findall(r"[a-z0-9_]+", lowered))
        cjk = "".join(char for char in lowered if chr(0x4E00) <= char <= chr(0x9FFF))
        tokens.update(cjk)
        tokens.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
        return {token for token in tokens if token}

    @classmethod
    def _lexical_score(cls, query_tokens: set[str], text: str) -> int:
        if not query_tokens:
            return 0
        text_tokens = cls._tokens(text)
        return sum(1 for token in query_tokens if token in text_tokens)

    @staticmethod
    def _result_payload(row: MemoryRow, rank: int, lexical_score: int) -> dict[str, Any]:
        content = row.content or {}
        body = content.get("text", content)
        return {
            "memory_id": row.id,
            "project_id": row.project_id,
            "level": row.level,
            "scope": row.scope,
            "memory_type": row.memory_type,
            "title": row.title or "",
            "content": body,
            "rank": rank,
            "rrf_score": 1.0 / (_RRF_K + rank),
            "source_ids": content.get("source_ids", []) if isinstance(content, dict) else [],
            "lexical_score": lexical_score,
        }

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, math.ceil(len(text) / 6))