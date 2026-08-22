"""V1.7 会话窗口收集、封口及受限上下文构建。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .persistence.db_models import MemoryRow, MessageRow, ProjectRow, SessionRow
from .persistence.v17_models import ProjectMemoryWindowPolicyRow, MemoryWindowMessageRow, MemoryWindowRow


class MemoryWindowService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get_policy(self, project_id: int) -> ProjectMemoryWindowPolicyRow:
        with self.session_factory() as session:
            row = session.get(ProjectMemoryWindowPolicyRow, project_id)
            if row is None:
                row = ProjectMemoryWindowPolicyRow(project_id=project_id)
                session.add(row)
                session.commit()
            else:
                session.refresh(row)
            session.expunge(row)
            return row

    def update_policy(self, project_id: int, *, enabled: bool | None = None, manual_apply_enabled: bool | None = None, max_messages: int | None = None, max_input_chars: int | None = None, updated_by: str = "admin") -> ProjectMemoryWindowPolicyRow:
        with self.session_factory() as session:
            row = session.get(ProjectMemoryWindowPolicyRow, project_id) or ProjectMemoryWindowPolicyRow(project_id=project_id)
            if enabled is not None:
                row.enabled = bool(enabled)
            if manual_apply_enabled is not None:
                row.manual_apply_enabled = bool(manual_apply_enabled)
            if max_messages is not None:
                if max_messages <= 0:
                    raise ValueError("max_messages 必须大于 0")
                row.max_messages = max_messages
            if max_input_chars is not None:
                if max_input_chars <= 0:
                    raise ValueError("max_input_chars 必须大于 0")
                row.max_input_chars = max_input_chars
            row.updated_by = updated_by[:255]
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def append_message(self, *, project_id: int, session_id: int, message_id: int) -> MemoryWindowRow:
        """幂等地把同一会话消息加入唯一 open window。"""
        with self.session_factory() as session:
            project = session.get(ProjectRow, project_id)
            conversation = session.get(SessionRow, session_id)
            message = session.get(MessageRow, message_id)
            if project is None or conversation is None or message is None or conversation.project_id != project_id or message.project_id != project_id or message.session_id != session_id:
                raise ValueError("消息、会话与项目不一致")
            policy = session.get(ProjectMemoryWindowPolicyRow, project_id)
            if policy is None or not policy.enabled:
                raise ValueError("V1.7 Memory Window 未启用")
            window = session.scalar(select(MemoryWindowRow).where(MemoryWindowRow.project_id == project_id, MemoryWindowRow.session_id == session_id, MemoryWindowRow.status == "open").with_for_update())
            if window is None:
                window = MemoryWindowRow(project_id=project_id, session_id=session_id, session_key=conversation.session_key)
                session.add(window)
                try:
                    session.flush()
                except IntegrityError:
                    # 另一个事务已经创建了同一会话的 open window；恢复为该对象，
                    # 不把唯一键竞争暴露为永久失败。
                    session.rollback()
                    window = session.scalar(select(MemoryWindowRow).where(
                        MemoryWindowRow.project_id == project_id,
                        MemoryWindowRow.session_id == session_id,
                        MemoryWindowRow.status == "open",
                    ).with_for_update())
                    if window is None:
                        raise
            existing = session.scalar(select(MemoryWindowMessageRow).where(MemoryWindowMessageRow.window_id == window.id, MemoryWindowMessageRow.message_id == message_id))
            if existing is None:
                position = int(session.scalar(select(MemoryWindowMessageRow.position).where(MemoryWindowMessageRow.window_id == window.id).order_by(MemoryWindowMessageRow.position.desc()).limit(1)) or -1) + 1
                if position >= policy.max_messages:
                    raise ValueError("Memory Window 已达到消息上限，请先封口")
                session.add(MemoryWindowMessageRow(window_id=window.id, message_id=message_id, position=position, content_hash=message.content_hash))
            session.commit()
            session.refresh(window)
            session.expunge(window)
            return window

    def seal(self, *, project_id: int, session_id: int, sealed_by: str = "admin") -> MemoryWindowRow:
        with self.session_factory() as session:
            policy = session.get(ProjectMemoryWindowPolicyRow, project_id)
            if policy is None or not policy.enabled:
                raise ValueError("V1.7 Memory Window 未启用")
            window = session.scalar(select(MemoryWindowRow).where(MemoryWindowRow.project_id == project_id, MemoryWindowRow.session_id == session_id, MemoryWindowRow.status == "open").with_for_update())
            if window is None:
                existing = session.scalar(select(MemoryWindowRow).where(MemoryWindowRow.project_id == project_id, MemoryWindowRow.session_id == session_id, MemoryWindowRow.status.in_(("sealed", "processing", "completed"))).order_by(MemoryWindowRow.id.desc()))
                if existing is None:
                    raise LookupError("没有可封口的 Memory Window")
                # 即使窗口已经封口，也必须重新验证不可变 L0 证据；不能
                # 因为状态已改变就跳过内容哈希校验。
                digest = self._validate_messages(session, existing)
                if existing.input_hash != digest.hexdigest():
                    raise ValueError("窗口 input_hash 已漂移")
                session.expunge(existing)
                return existing
            digest = self._validate_messages(session, window)
            window.input_hash = digest.hexdigest()
            window.status = "sealed"
            window.sealed_at = datetime.now(timezone.utc)
            window.sealed_by = sealed_by[:255]
            session.commit()
            session.refresh(window)
            session.expunge(window)
            return window

    def build_input(self, window_id: int) -> dict[str, Any]:
        """只返回本项目 L0 消息及 published L1；不调用默认 build_context。"""
        with self.session_factory() as session:
            window = session.get(MemoryWindowRow, window_id)
            if window is None:
                raise LookupError("Memory Window 不存在")
            if window.status not in {"sealed", "processing", "completed"}:
                raise ValueError("Memory Window 尚未封口")
            policy = session.get(ProjectMemoryWindowPolicyRow, window.project_id)
            if policy is None or not policy.enabled:
                raise ValueError("V1.7 Memory Window 未启用")
            messages = []
            total_chars = 0
            rows = session.scalars(select(MemoryWindowMessageRow).where(MemoryWindowMessageRow.window_id == window.id).order_by(MemoryWindowMessageRow.position, MemoryWindowMessageRow.id)).all()
            digest = hashlib.sha256()
            for item in rows:
                message = session.get(MessageRow, item.message_id)
                if message is None or message.project_id != window.project_id:
                    raise ValueError("窗口消息证据已漂移")
                actual_hash = hashlib.sha256(message.content.encode("utf-8")).hexdigest()
                if actual_hash != str(message.content_hash) or actual_hash != str(item.content_hash):
                    raise ValueError("窗口消息内容哈希已漂移")
                digest.update(f"{item.position}:{item.message_id}:{actual_hash}\n".encode())
                total_chars += len(message.content)
                if total_chars > policy.max_input_chars:
                    raise ValueError("Memory Window 输入超过字符上限")
                messages.append({"message_id": message.id, "role": message.role, "content": message.content, "content_hash": message.content_hash})
            if window.input_hash != digest.hexdigest():
                raise ValueError("窗口 input_hash 已漂移")
            memories = session.scalars(select(MemoryRow).where(MemoryRow.project_id == window.project_id, MemoryRow.level == "L1", MemoryRow.scope == "project", MemoryRow.status == "published", MemoryRow.deprecated.is_(False)).order_by(MemoryRow.id)).all()
            l1 = [{"memory_id": item.id, "revision": int(getattr(item, "revision", 1)), "title": item.title, "content": item.content, "confidence": item.confidence} for item in memories]
            project = session.get(ProjectRow, window.project_id)
            return {"project_id": window.project_id, "project_key": project.project_key if project else "", "window_id": window.id, "input_hash": window.input_hash, "messages": messages, "published_l1": l1}

    @staticmethod
    def _validate_messages(session: Session, window: MemoryWindowRow):
        """实时校验窗口中每条 L0 的内容、消息哈希及窗口证据哈希。"""
        items = session.scalars(
            select(MemoryWindowMessageRow)
            .where(MemoryWindowMessageRow.window_id == window.id)
            .order_by(MemoryWindowMessageRow.position, MemoryWindowMessageRow.id)
        ).all()
        digest = hashlib.sha256()
        for item in items:
            message = session.get(MessageRow, item.message_id)
            if message is None or message.project_id != window.project_id:
                raise ValueError("窗口消息证据已漂移")
            actual_hash = hashlib.sha256(message.content.encode("utf-8")).hexdigest()
            if actual_hash != str(message.content_hash) or actual_hash != str(item.content_hash):
                raise ValueError("窗口消息内容哈希已漂移")
            digest.update(f"{item.position}:{item.message_id}:{actual_hash}\n".encode())
        return digest

    append_message_to_window = append_message
    seal_window = seal
    build_window_input = build_input


__all__ = ["MemoryWindowService"]
