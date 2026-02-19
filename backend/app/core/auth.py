"""Simple tenant-aware auth dependency for API routes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.logging import logger


security = HTTPBearer(auto_error=False)


@dataclass
class TenantContext:
    tenant_id: str
    authenticated: bool
    actor: str
    role: str


SUPPORTED_ROLES = {"dispatcher", "billing", "admin"}


def _normalize_role(value: str | None) -> str:
    role = (value or "").strip().lower()
    if not role:
        return "admin"
    if role not in SUPPORTED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported role '{value}'. Expected one of: {sorted(SUPPORTED_ROLES)}",
        )
    return role


def _parse_tenant_tokens(raw: str) -> Dict[str, str]:
    """Parse `token:tenant` comma-separated values from env."""
    mapping: Dict[str, str] = {}
    if not raw.strip():
        return mapping

    for segment in raw.split(","):
        item = segment.strip()
        if not item:
            continue
        if ":" not in item:
            logger.warning("Ignoring malformed tenant token mapping entry", entry=item)
            continue
        token, tenant = item.split(":", 1)
        token = token.strip()
        tenant = tenant.strip()
        if token and tenant:
            mapping[token] = tenant
    return mapping


def get_tenant_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_actor_role: str | None = Header(default=None, alias="X-Actor-Role"),
) -> TenantContext:
    """Resolve tenant context from bearer token or default tenant header."""
    settings = get_settings()
    default_tenant = (x_tenant_id or settings.default_tenant_id or "demo").strip() or "demo"

    if not settings.auth_enabled:
        return TenantContext(
            tenant_id=default_tenant,
            authenticated=False,
            actor="anonymous",
            role=_normalize_role(x_actor_role),
        )

    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
        )

    token_map = _parse_tenant_tokens(settings.tenant_tokens)
    tenant_id = token_map.get(credentials.credentials.strip())
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bearer token",
        )

    if x_tenant_id and x_tenant_id.strip() and x_tenant_id.strip() != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token tenant mismatch",
        )

    return TenantContext(
        tenant_id=tenant_id,
        authenticated=True,
        actor="token",
        role=_normalize_role(x_actor_role),
    )


def require_roles(*allowed_roles: str):
    """Dependency factory that enforces role-based access control."""
    allowed = {role.strip().lower() for role in allowed_roles if role.strip()}
    if not allowed:
        raise ValueError("At least one role is required")

    def _guard(context: TenantContext = Depends(get_tenant_context)) -> TenantContext:
        if context.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{context.role}' not permitted for this operation",
            )
        return context

    return _guard
