from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import ApiKeyRow, ProjectRow


class TokenAuthenticationError(PermissionError):
    pass


class PermissionDenied(PermissionError):
    pass


class ProjectAccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    project_key: str
    permissions: frozenset[str]
    display_name: str | None = None
    auth_type: str | None = None
    expires_at: datetime | None = None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_admin_session(username: str, project_key: str = "*", ttl_seconds: int = 8 * 60 * 60) -> str:
    secret = os.environ.get("CODEX_MEMORY_ADMIN_SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("CODEX_MEMORY_ADMIN_SESSION_SECRET is not configured")
    payload = {
        "sub": username,
        "display_name": username,
        "auth_type": "session",
        "project_key": project_key,
        "permissions": ["admin", "read"],
        "exp": int(time.time()) + ttl_seconds,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"cm1.{encoded}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


def authenticate_admin_session(token: str) -> Principal | None:
    if not token.startswith("cm1."):
        return None
    secret = os.environ.get("CODEX_MEMORY_ADMIN_SESSION_SECRET", "")
    try:
        _, encoded, signature = token.split(".", 2)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not secret or not hmac.compare_digest(actual, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        expires_at = int(payload["exp"])
        if expires_at <= int(time.time()):
            return None
        return Principal(
            project_key=str(payload["project_key"]),
            permissions=frozenset(payload["permissions"]),
            display_name=str(payload.get("display_name") or payload.get("sub") or "") or None,
            auth_type=str(payload.get("auth_type") or "session"),
            expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        )
    except (ValueError, KeyError, TypeError, OverflowError, OSError, json.JSONDecodeError):
        return None


def authenticate_bearer(session_factory: sessionmaker[Session], token: str) -> Principal:
    session_principal = authenticate_admin_session(token)
    if session_principal is not None:
        return session_principal
    token_hash = hash_token(token)
    with session_factory() as session:
        row = session.execute(
            select(ApiKeyRow, ProjectRow)
            .join(ProjectRow, ProjectRow.id == ApiKeyRow.project_id)
            .where(ApiKeyRow.token_hash == token_hash, ApiKeyRow.status == "active")
        ).first()
        if row is None:
            raise TokenAuthenticationError("Bearer 令牌无效")
        api_key, project = row
        return Principal(
            project_key=project.project_key,
            permissions=frozenset(api_key.permissions),
            auth_type="api_key",
        )


def require_project_access(principal: Principal, project_key: str) -> None:
    if "admin" not in principal.permissions and principal.project_key != project_key:
        raise ProjectAccessDenied(f"令牌无权访问项目：{project_key}")


def require_permission(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions and "admin" not in principal.permissions:
        raise PermissionDenied(f"缺少权限：{permission}")
