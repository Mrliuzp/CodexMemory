from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from .db_models import ProjectProcessingPolicyRow
from .v11_models import DailyTokenUsageRow


class BudgetExceededError(Exception):
    pass


class ProviderBudgetTracker:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def check(self, project_id: int, token_type: str, estimated_tokens: int) -> bool:
        if estimated_tokens <= 0:
            return True
        with self.session_factory() as session:
            policy = session.get(ProjectProcessingPolicyRow, project_id)
            if policy is None:
                return True
            limit = 0
            if token_type.startswith("embedding"):
                limit = policy.daily_embedding_token_budget or 0
            elif token_type.startswith("llm"):
                limit = policy.daily_llm_token_budget or 0
            if limit <= 0:
                return True
            today = date.today()
            usage = session.scalar(
                select(DailyTokenUsageRow).where(
                    DailyTokenUsageRow.project_id == project_id,
                    DailyTokenUsageRow.usage_date == today,
                    DailyTokenUsageRow.token_type == token_type,
                )
            )
            used = usage.tokens_used if usage is not None else 0
            return (used + estimated_tokens) <= limit

    def record(self, project_id: int, token_type: str, tokens: int) -> None:
        if tokens <= 0:
            return
        with self.session_factory() as session:
            today = date.today()
            stmt = (
                update(DailyTokenUsageRow)
                .where(
                    DailyTokenUsageRow.project_id == project_id,
                    DailyTokenUsageRow.usage_date == today,
                    DailyTokenUsageRow.token_type == token_type,
                )
                .values(
                    tokens_used=DailyTokenUsageRow.tokens_used + tokens,
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            result = session.execute(stmt)
            if result.rowcount == 0:
                session.add(
                    DailyTokenUsageRow(
                        project_id=project_id,
                        usage_date=today,
                        token_type=token_type,
                        tokens_used=tokens,
                    )
                )
            session.commit()

    @staticmethod
    def estimate_tokens(texts: list[str]) -> int:
        return sum(max(1, math.ceil(len(text) / 4)) for text in texts)

    def wrap_embedding_backend(
        self,
        project_id: int,
        token_type: str,
        backend: Callable[[list[str]], list[list[float]]],
    ) -> Callable[[list[str]], list[list[float]]]:
        def wrapped(texts: list[str]) -> list[list[float]]:
            estimated = self.estimate_tokens(texts)
            if not self.check(project_id, token_type, estimated):
                raise BudgetExceededError(
                    f"daily {token_type} budget exceeded for project {project_id}"
                )
            result = backend(texts)
            self.record(project_id, token_type, estimated)
            return result
        return wrapped