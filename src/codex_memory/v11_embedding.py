from __future__ import annotations

import hashlib
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    EmbeddingProfileRow,
    MemoryChunkRow,
    MemoryEmbeddingVectorRow,
    MemoryRow,
    ProjectRow,
)


class EmbeddingProfileService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_profile(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        dimension: int,
        chunker_version: str,
        content_normalization_version: str,
        normalization: str = "l2",
    ) -> EmbeddingProfileRow:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        with self.session_factory() as session:
            profile = EmbeddingProfileRow(
                name=name,
                provider=provider,
                model=model,
                dimension=dimension,
                normalization=normalization,
                chunker_version=chunker_version,
                content_normalization_version=content_normalization_version,
                status="draft",
            )
            session.add(profile)
            session.commit()
            return profile

    def validate_vector(self, profile_id: int, vector: list[float]) -> None:
        with self.session_factory() as session:
            profile = session.get(EmbeddingProfileRow, profile_id)
            if profile is None:
                raise LookupError(f"profile does not exist: {profile_id}")
            if len(vector) != profile.dimension:
                raise ValueError(f"embedding dimension {len(vector)} does not match profile dimension {profile.dimension}")

    def embed_query(self, profile_id: int, text: str) -> list[float]:
        with self.session_factory() as session:
            profile = session.get(EmbeddingProfileRow, profile_id)
            if profile is None:
                raise LookupError(f"profile does not exist: {profile_id}")
            return self._vector(text, profile.dimension, profile.normalization)

    def backfill_memory(self, project_id: int, memory_id: int, profile_id: int) -> list[MemoryEmbeddingVectorRow]:
        with self.session_factory() as session:
            project = session.get(ProjectRow, project_id)
            memory = session.get(MemoryRow, memory_id)
            profile = session.get(EmbeddingProfileRow, profile_id)
            if project is None:
                raise LookupError(f"project does not exist: {project_id}")
            if memory is None or memory.project_id != project_id:
                raise LookupError(f"memory does not belong to project: {memory_id}")
            if profile is None:
                raise LookupError(f"profile does not exist: {profile_id}")
            text = self._memory_text(memory)
            chunks = self._chunks(text)
            vectors: list[MemoryEmbeddingVectorRow] = []
            for index, chunk_text in enumerate(chunks):
                chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                chunk = session.scalar(
                    select(MemoryChunkRow).where(
                        MemoryChunkRow.memory_id == memory_id,
                        MemoryChunkRow.memory_version == 1,
                        MemoryChunkRow.chunk_index == index,
                    )
                )
                if chunk is None:
                    chunk = MemoryChunkRow(
                        memory_id=memory_id,
                        memory_version=1,
                        chunk_index=index,
                        content=chunk_text,
                        content_hash=chunk_hash,
                        start_char=0,
                        end_char=len(chunk_text),
                        chunker_version=profile.chunker_version,
                    )
                    session.add(chunk)
                    session.flush()
                vector = session.scalar(
                    select(MemoryEmbeddingVectorRow).where(
                        MemoryEmbeddingVectorRow.chunk_id == chunk.id,
                        MemoryEmbeddingVectorRow.embedding_profile_id == profile_id,
                    )
                )
                if vector is None:
                    vector = MemoryEmbeddingVectorRow(
                        project_id=project_id,
                        memory_id=memory_id,
                        chunk_id=chunk.id,
                        embedding_profile_id=profile_id,
                        embedding=self._vector(chunk_text, profile.dimension, profile.normalization),
                        dimension=profile.dimension,
                        content_hash=chunk_hash,
                    )
                    session.add(vector)
                    session.flush()
                vectors.append(vector)
            session.commit()
            return vectors

    @staticmethod
    def _memory_text(memory: MemoryRow) -> str:
        if isinstance(memory.content, dict):
            return str(memory.content.get("text", memory.content))
        return str(memory.content)

    @staticmethod
    def _chunks(text: str, max_chars: int = 512) -> list[str]:
        normalized = " ".join(text.split())
        if not normalized:
            return [""]
        return [normalized[index : index + max_chars] for index in range(0, len(normalized), max_chars)]

    @staticmethod
    def _vector(text: str, dimension: int, normalization: str) -> list[float]:
        values: list[float] = []
        seed = text.encode("utf-8")
        counter = 0
        while len(values) < dimension:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            values.extend((byte / 127.5) - 1.0 for byte in digest)
            counter += 1
        vector = values[:dimension]
        if normalization == "l2":
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
        return vector