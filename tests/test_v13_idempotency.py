from __future__ import annotations


def test_idempotency_key_builder_normalizes_parts_and_is_stable() -> None:
    from codex_memory.idempotency import IdempotencyKeyBuilder

    builder = IdempotencyKeyBuilder(" Demo Project ")
    assert builder.build("extract_memory_candidate", "message", "msg-1", "memory-extractor-v1") == (
        "demo-project.extract_memory_candidate.message.msg-1.memory-extractor-v1"
    )
    assert builder.build("extract_memory_candidate", "message", "msg-1", "memory-extractor-v1") == builder.build(
        "extract_memory_candidate", "message", "msg-1", "memory-extractor-v1"
    )


def test_idempotency_key_builder_rejects_empty_or_unsafe_parts() -> None:
    import pytest

    from codex_memory.idempotency import IdempotencyKeyBuilder

    with pytest.raises(ValueError):
        IdempotencyKeyBuilder("")
    with pytest.raises(ValueError):
        IdempotencyKeyBuilder("demo").build("", "message", "1", "v1")
    with pytest.raises(ValueError):
        IdempotencyKeyBuilder("demo").build("op", "message", "", "v1")

