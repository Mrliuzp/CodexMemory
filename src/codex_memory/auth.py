from __future__ import annotations

import hashlib
from dataclasses import dataclass

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


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate_bearer(session_factory: sessionmaker[Session], token: str) -> Principal:
    token_hash = hash_token(token)
    with session_factory() as session:
        row = session.execute(
            select(ApiKeyRow, ProjectRow)
            .join(ProjectRow, ProjectRow.id == ApiKeyRow.project_id)
            .where(ApiKeyRow.token_hash == token_hash, ApiKeyRow.status == "active")
        ).first()
        if row is None:
            raise TokenAuthenticationError("invalid bearer token")
        api_key, project = row
        return Principal(project_key=project.project_key, permissions=frozenset(api_key.permissions))


def require_project_access(principal: Principal, project_key: str) -> None:
    if "admin" not in principal.permissions and principal.project_key != project_key:
        raise ProjectAccessDenied(f"token cannot access project: {project_key}")


def require_permission(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions and "admin" not in principal.permissions:
        raise PermissionDenied(f"missing permission: {permission}")
