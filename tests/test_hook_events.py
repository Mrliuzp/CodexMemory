import pytest

from codex_memory.hook_events import HookEventError, parse_assistant_event, parse_user_event


def test_user_event_builds_project_scoped_key() -> None:
    event = parse_user_event(
        {"cwd": "G:/erp", "session_id": "s1", "turn_id": "t1", "prompt": "修改订单"},
        project_id="erp",
    )

    assert event.event_key == "codex:erp:s1:t1:user"
    assert event.role == "user"
    assert event.content == "修改订单"
    assert event.to_append_payload()["metadata"] == {"turn_id": "t1"}


def test_assistant_event_ignores_empty_message() -> None:
    assert parse_assistant_event(
        {"cwd": "G:/erp", "session_id": "s1", "turn_id": "t1", "last_assistant_message": ""},
        project_id="erp",
    ) is None


@pytest.mark.parametrize("field", ["cwd", "session_id", "turn_id", "prompt"])
def test_user_event_rejects_missing_required_field(field: str) -> None:
    payload = {"cwd": "G:/erp", "session_id": "s1", "turn_id": "t1", "prompt": "修改订单"}
    payload.pop(field)

    with pytest.raises(HookEventError, match="缺少或无效"):
        parse_user_event(payload, project_id="erp")


def test_assistant_event_rejects_non_string_content() -> None:
    with pytest.raises(HookEventError, match="缺少或无效"):
        parse_assistant_event(
            {"cwd": "G:/erp", "session_id": "s1", "turn_id": "t1", "last_assistant_message": 1},
            project_id="erp",
        )