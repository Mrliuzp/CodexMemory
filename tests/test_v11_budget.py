from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select


def _factory():
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectProcessingPolicyRow, ProjectRow, V11Base

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(ProjectProcessingPolicyRow(project_id=project.id, daily_embedding_token_budget=100))
        session.commit()
    return factory


def test_budget_allows_when_under_limit() -> None:
    from codex_memory.v11_budget import ProviderBudgetTracker

    factory = _factory()
    tracker = ProviderBudgetTracker(factory)
    assert tracker.check(1, "embedding_input", 50) is True
    assert tracker.check(1, "embedding_input", 100) is True


def test_budget_blocks_when_over_limit() -> None:
    from codex_memory.v11_budget import ProviderBudgetTracker

    factory = _factory()
    tracker = ProviderBudgetTracker(factory)
    tracker.record(1, "embedding_input", 80)
    assert tracker.check(1, "embedding_input", 30) is False


def test_budget_allows_different_token_type() -> None:
    from codex_memory.v11_budget import ProviderBudgetTracker

    factory = _factory()
    tracker = ProviderBudgetTracker(factory)
    tracker.record(1, "llm_input", 1000)
    assert tracker.check(1, "embedding_input", 50) is True


def test_zero_budget_means_unlimited() -> None:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectProcessingPolicyRow, ProjectRow, V11Base

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()

    from codex_memory.v11_budget import ProviderBudgetTracker

    assert ProviderBudgetTracker(factory).check(1, "embedding_input", 99999) is True


def test_wrapped_backend_enforces_budget() -> None:
    from codex_memory.v11_budget import BudgetExceededError, ProviderBudgetTracker

    factory = _factory()
    tracker = ProviderBudgetTracker(factory)
    tracker.record(1, "embedding_input", 80)

    def backend(texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    wrapped = tracker.wrap_embedding_backend(1, "embedding_input", backend)
    wrapped(["hello"])
    with pytest.raises(BudgetExceededError):
        wrapped(["a" * 200])


def test_daily_token_usage_model_stores_and_upserts() -> None:
    from codex_memory.v11_models import DailyTokenUsageRow

    factory = _factory()
    from codex_memory.v11_budget import ProviderBudgetTracker

    tracker = ProviderBudgetTracker(factory)
    tracker.record(1, "embedding_input", 50)
    tracker.record(1, "embedding_input", 30)

    with factory() as session:
        usage = session.scalar(select(DailyTokenUsageRow).where(DailyTokenUsageRow.project_id == 1))
        assert usage is not None
        assert usage.tokens_used == 80