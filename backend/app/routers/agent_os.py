"""API routes for SHAMS Agent OS orchestration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.core.auth import TenantContext, get_tenant_context, require_roles
from app.core.logging import logger
from app.models.agent_os import (
    AgentApprovalDecisionRequest,
    AgentPolicyPatchRequest,
    AgentRunRequest,
)
from app.services.agent_os import agent_os_service
from app.services.ops_state import ops_state_store


router = APIRouter(prefix="/agent-os", tags=["agent-os"])


def _idempotency_lookup(context: TenantContext, operation: str, key: str | None):
    if not key:
        return None
    return ops_state_store.get_idempotent(context.tenant_id, f"{operation}:{key.strip()}")


def _idempotency_store(context: TenantContext, operation: str, key: str | None, response: dict):
    if not key:
        return
    ops_state_store.set_idempotent(context.tenant_id, f"{operation}:{key.strip()}", response)


@router.post("/runs")
async def create_run(
    request: AgentRunRequest,
    context: TenantContext = Depends(require_roles("admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, "agent_os_run", idempotency_key)
    if cached:
        return cached
    try:
        response = await agent_os_service.run_objective(
            request=request,
            tenant_id=context.tenant_id,
            actor=context.actor,
            role=context.role,
        )
        payload = response.model_dump(mode="json")
        _idempotency_store(context, "agent_os_run", idempotency_key, payload)
        return payload
    except Exception as exc:
        logger.error("Agent run creation failed", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/runs")
def list_runs(
    limit: int = Query(default=50, ge=1, le=500),
    context: TenantContext = Depends(require_roles("admin")),
):
    return {
        "items": [row.model_dump(mode="json") for row in agent_os_service.list_runs(context.tenant_id, limit=limit)],
        "tenant_id": context.tenant_id,
    }


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    context: TenantContext = Depends(require_roles("admin")),
):
    try:
        return agent_os_service.get_run(run_id, tenant_id=context.tenant_id).model_dump(mode="json")
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.get("/runs/{run_id}/timeline")
def get_run_timeline(
    run_id: str,
    context: TenantContext = Depends(require_roles("admin")),
):
    try:
        return agent_os_service.run_timeline(run_id, tenant_id=context.tenant_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.get("/approvals/pending")
def pending_approvals(
    limit: int = Query(default=100, ge=1, le=1000),
    context: TenantContext = Depends(require_roles("admin")),
):
    return {
        "items": [row.model_dump(mode="json") for row in agent_os_service.list_pending_approvals(context.tenant_id, limit=limit)],
        "tenant_id": context.tenant_id,
    }


@router.post("/runs/{run_id}/approve")
async def decide_approval(
    run_id: str,
    request: AgentApprovalDecisionRequest,
    context: TenantContext = Depends(require_roles("admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, f"agent_os_approval:{run_id}:{request.approval_id}", idempotency_key)
    if cached:
        return cached
    try:
        response = await agent_os_service.decide_approval(
            run_id=run_id,
            request=request,
            tenant_id=context.tenant_id,
            actor=context.actor,
            role=context.role,
        )
        payload = response.model_dump(mode="json")
        _idempotency_store(context, f"agent_os_approval:{run_id}:{request.approval_id}", idempotency_key, payload)
        return payload
    except KeyError:
        raise HTTPException(status_code=404, detail="Run or approval not found")
    except Exception as exc:
        logger.error("Approval decision failed", run_id=run_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/policies")
def list_policies(
    context: TenantContext = Depends(require_roles("admin")),
):
    return {
        "items": [row.model_dump(mode="json") for row in agent_os_service.list_policies()],
        "tenant_id": context.tenant_id,
    }


@router.patch("/policies/{policy_id}")
def patch_policy(
    policy_id: str,
    request: AgentPolicyPatchRequest,
    context: TenantContext = Depends(require_roles("admin")),
):
    try:
        return agent_os_service.patch_policy(policy_id, request).model_dump(mode="json")
    except KeyError:
        raise HTTPException(status_code=404, detail="Policy not found")
    except Exception as exc:
        logger.error("Policy update failed", policy_id=policy_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/metrics")
def run_metrics(
    context: TenantContext = Depends(require_roles("admin")),
):
    return agent_os_service.run_metrics(context.tenant_id).model_dump(mode="json")
