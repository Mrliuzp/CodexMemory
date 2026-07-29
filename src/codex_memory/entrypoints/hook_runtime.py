"""提供稳定的 Hook 运行入口。"""

from ..codex_hooks import (
    handle_assistant_stop,
    handle_event,
    handle_post_tool_use,
    handle_pre_tool_use,
    handle_session_end,
    handle_user_prompt,
)

handle_stop = handle_assistant_stop
