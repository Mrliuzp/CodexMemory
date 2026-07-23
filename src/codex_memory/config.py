from .persistence.config import *


def is_placeholder_value(value: str | None) -> bool:
    """判断配置值是否为空或仍为示例占位符。"""
    normalized = (value or "").strip().lower()
    return not normalized or normalized.startswith(("change-me", "change_me"))
