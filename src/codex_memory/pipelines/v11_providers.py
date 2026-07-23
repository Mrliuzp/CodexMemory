from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from .db_models import EmbeddingProfileRow, ProjectProcessingPolicyRow


class ProviderPolicyError(Exception):
    pass


class RetryableProviderError(Exception):
    pass


from .v11_budget import BudgetExceededError, ProviderBudgetTracker

class ProfileEmbeddingProvider:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.budget_tracker = ProviderBudgetTracker(session_factory)

    def embed_documents(
        self,
        project_id: int,
        profile_id: int,
        texts: list[str],
        backend: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> list[list[float]]:
        with self.session_factory() as session:
            profile = session.get(EmbeddingProfileRow, profile_id)
            if profile is None:
                raise ProviderPolicyError(f"embedding profile does not exist: {profile_id}")
            policy = session.get(ProjectProcessingPolicyRow, project_id)
            is_remote = profile.provider not in ("local", "test", "hash")
            if is_remote and policy is not None and not policy.remote_embedding_allowed:
                raise ProviderPolicyError("remote embedding is blocked by project policy")
            if is_remote and policy is not None and policy.allowed_embedding_providers and profile.provider not in policy.allowed_embedding_providers:
                raise ProviderPolicyError(f"provider {profile.provider} is not in allowed list")

        if backend is not None:
            backend = self.budget_tracker.wrap_embedding_backend(project_id, "embedding_input", backend)
            try:
                result = backend(texts)
            except (TimeoutError, ConnectionError, OSError) as error:
                raise RetryableProviderError(f"remote embedding timeout: {error}") from error
            if len(result) != len(texts):
                raise ProviderPolicyError(f"expected {len(texts)} vectors, got {len(result)}")
            for vec in result:
                if len(vec) != profile.dimension:
                    raise ProviderPolicyError(
                        f"embedding dimension {len(vec)} does not match profile dimension {profile.dimension}"
                    )
            return result

        # 本地确定性回退
        from .v11_embedding import EmbeddingProfileService

        return [
            EmbeddingProfileService._vector(text, profile.dimension, "l2")
            for text in texts
        ]