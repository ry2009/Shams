"""API routes for SHAMS autonomous dispatch/ticketing/billing operations."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header

from app.core.auth import TenantContext, get_tenant_context, require_roles
from app.core.config import get_settings
from app.core.logging import logger
from app.models.ops import (
    AutonomyRunRequest,
    AutonomyRunResponse,
    CopilotQueryRequest,
    DemoPackSeedRequest,
    DemoPackSeedResponse,
    DispatchBoardResponse,
    LoadAssignmentRequest,
    LoadCreateRequest,
    LoadStatus,
    LoadStatusTransitionRequest,
    LoadUpdateRequest,
    McleodExportResponse,
    OpsMetricsSnapshot,
    SamsaraSyncRequest,
    SyntheticScenarioRequest,
    TicketDecisionRequest,
    TicketReviewRequest,
    TicketReviewResult,
)
from app.services.ops_engine import ops_engine
from app.services.ops_state import ops_state_store

router = APIRouter(prefix="/ops", tags=["ops"])


def _idempotency_lookup(context: TenantContext, operation: str, key: str | None):
    if not key:
        return None
    return ops_state_store.get_idempotent(context.tenant_id, f"{operation}:{key.strip()}")


def _idempotency_store(context: TenantContext, operation: str, key: str | None, response: dict):
    if not key:
        return
    ops_state_store.set_idempotent(context.tenant_id, f"{operation}:{key.strip()}", response)


@router.get("/dispatch/board", response_model=DispatchBoardResponse)
def get_dispatch_board(
    status: Optional[LoadStatus] = Query(default=None),
    context: TenantContext = Depends(get_tenant_context),
):
    return ops_engine.dispatch_board(context.tenant_id, status=status)


@router.post("/integrations/driver-app/dispatch/send/{load_id}")
def driver_app_dispatch_send(
    load_id: str,
    context: TenantContext = Depends(require_roles("dispatcher", "admin")),
):
    try:
        return ops_engine.dispatch_send(context.tenant_id, load_id=load_id, actor=context.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="Load not found")
    except Exception as exc:
        logger.error("Failed to send driver-app dispatch", load_id=load_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/integrations/driver-app/dispatch/send-batch")
def driver_app_dispatch_send_batch(
    limit: int = Query(default=10, ge=1, le=50),
    context: TenantContext = Depends(require_roles("dispatcher", "admin")),
):
    try:
        return ops_engine.dispatch_send_batch(context.tenant_id, actor=context.actor, limit=limit)
    except Exception as exc:
        logger.error("Failed to send driver-app dispatch batch", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/integrations/driver-app/dispatch/feed")
def driver_app_dispatch_feed(
    load_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    context: TenantContext = Depends(get_tenant_context),
):
    return ops_engine.dispatch_feed(context.tenant_id, load_id=load_id, limit=limit)


@router.post("/dispatch/loads")
def create_load(
    request: LoadCreateRequest,
    context: TenantContext = Depends(require_roles("dispatcher", "admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, "create_load", idempotency_key)
    if cached:
        return cached
    try:
        response = ops_engine.create_load(request, tenant_id=context.tenant_id, actor=context.actor)
        _idempotency_store(context, "create_load", idempotency_key, response)
        return response
    except Exception as exc:
        logger.error("Failed to create load", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/dispatch/loads/{load_id}")
def update_load(
    load_id: str,
    request: LoadUpdateRequest,
    context: TenantContext = Depends(require_roles("dispatcher", "admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, f"update_load:{load_id}", idempotency_key)
    if cached:
        return cached
    try:
        response = ops_engine.update_load(load_id, request, tenant_id=context.tenant_id, actor=context.actor)
        _idempotency_store(context, f"update_load:{load_id}", idempotency_key, response)
        return response
    except KeyError:
        raise HTTPException(status_code=404, detail="Load not found")
    except Exception as exc:
        logger.error("Failed to update load", load_id=load_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/dispatch/loads/{load_id}/status")
def transition_load_status(
    load_id: str,
    request: LoadStatusTransitionRequest,
    context: TenantContext = Depends(require_roles("dispatcher", "admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, f"transition_status:{load_id}:{request.status.value}", idempotency_key)
    if cached:
        return cached
    try:
        response = ops_engine.transition_load_status(
            load_id=load_id,
            request=request,
            tenant_id=context.tenant_id,
            actor=context.actor,
        )
        _idempotency_store(context, f"transition_status:{load_id}:{request.status.value}", idempotency_key, response)
        return response
    except KeyError:
        raise HTTPException(status_code=404, detail="Load not found")
    except Exception as exc:
        logger.error("Failed to transition load status", load_id=load_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/dispatch/assign")
def assign_load(
    request: LoadAssignmentRequest,
    context: TenantContext = Depends(require_roles("dispatcher", "admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, f"assign_load:{request.load_id}", idempotency_key)
    if cached:
        return cached
    try:
        response = ops_engine.assign_load(request, tenant_id=context.tenant_id, actor=context.actor)
        _idempotency_store(context, f"assign_load:{request.load_id}", idempotency_key, response)
        return response
    except KeyError:
        raise HTTPException(status_code=404, detail="Load not found")
    except Exception as exc:
        logger.error("Failed to assign load", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tickets/review", response_model=TicketReviewResult)
async def review_ticket(
    request: TicketReviewRequest,
    context: TenantContext = Depends(require_roles("dispatcher", "billing", "admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, f"review_ticket:{request.load_id}", idempotency_key)
    if cached:
        return cached
    try:
        response = await ops_engine.review_ticket(request, tenant_id=context.tenant_id, actor=context.actor)
        _idempotency_store(
            context,
            f"review_ticket:{request.load_id}",
            idempotency_key,
            response.model_dump(mode="json"),
        )
        return response
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to review ticket", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/tickets/queue")
def ticket_queue(
    status: Optional[str] = Query(default=None),
    context: TenantContext = Depends(get_tenant_context),
):
    return {
        "items": ops_engine.ticket_queue(context.tenant_id, status=status),
        "tenant_id": context.tenant_id,
    }


@router.get("/tickets/load/{load_id}")
def ticket_dossier(
    load_id: str,
    context: TenantContext = Depends(get_tenant_context),
):
    try:
        return ops_engine.ticket_dossier(context.tenant_id, load_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Load not found")


@router.post("/tickets/{review_id}/decision")
def ticket_decision(
    review_id: str,
    request: TicketDecisionRequest,
    context: TenantContext = Depends(require_roles("billing", "admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, f"ticket_decision:{review_id}", idempotency_key)
    if cached:
        return cached
    try:
        response = ops_engine.apply_ticket_decision(
            review_id,
            request,
            tenant_id=context.tenant_id,
            actor=context.actor,
        )
        _idempotency_store(context, f"ticket_decision:{review_id}", idempotency_key, response)
        return response
    except KeyError:
        raise HTTPException(status_code=404, detail="Review not found")
    except Exception as exc:
        logger.error("Failed to apply ticket decision", review_id=review_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/billing/readiness")
def billing_readiness(
    context: TenantContext = Depends(get_tenant_context),
):
    return {
        "items": ops_engine.billing_readiness(context.tenant_id),
        "tenant_id": context.tenant_id,
    }


@router.post("/integrations/mcleod/export/{load_id}", response_model=McleodExportResponse)
def create_mcleod_export(
    load_id: str,
    context: TenantContext = Depends(require_roles("billing", "admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, f"mcleod_export:{load_id}", idempotency_key)
    if cached:
        return cached
    try:
        response = ops_engine.create_mcleod_export(load_id, tenant_id=context.tenant_id, actor=context.actor)
        _idempotency_store(context, f"mcleod_export:{load_id}", idempotency_key, response)
        return response
    except KeyError:
        raise HTTPException(status_code=404, detail="Load not found")
    except Exception as exc:
        logger.error("McLeod export failed", load_id=load_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/integrations/mcleod/ledger")
def list_mcleod_ledger(
    context: TenantContext = Depends(get_tenant_context),
):
    return {
        "items": ops_engine.list_mcleod_exports(context.tenant_id),
        "tenant_id": context.tenant_id,
    }


@router.post("/integrations/mcleod/replay/{export_id}")
def replay_mcleod_export(
    export_id: str,
    context: TenantContext = Depends(require_roles("admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, f"mcleod_replay:{export_id}", idempotency_key)
    if cached:
        return cached
    try:
        response = ops_engine.replay_mcleod_export(export_id, tenant_id=context.tenant_id, actor=context.actor)
        _idempotency_store(context, f"mcleod_replay:{export_id}", idempotency_key, response)
        return response
    except KeyError:
        raise HTTPException(status_code=404, detail="Export not found")


@router.post("/integrations/samsara/sync")
def sync_samsara(
    request: SamsaraSyncRequest,
    context: TenantContext = Depends(require_roles("dispatcher", "admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cache_scope = f"samsara_sync:{','.join(request.load_ids) if request.load_ids else 'all'}:{request.hours_back}"
    cached = _idempotency_lookup(context, cache_scope, idempotency_key)
    if cached:
        return cached
    try:
        response = ops_engine.sync_samsara(request, tenant_id=context.tenant_id, actor=context.actor)
        _idempotency_store(context, cache_scope, idempotency_key, response)
        return response
    except Exception as exc:
        logger.error("Samsara sync failed", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/loads/{load_id}/timeline")
def load_timeline(
    load_id: str,
    context: TenantContext = Depends(get_tenant_context),
):
    try:
        return ops_engine.timeline(context.tenant_id, load_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Load not found")


@router.post("/copilot/query")
async def copilot_query(
    request: CopilotQueryRequest,
    context: TenantContext = Depends(get_tenant_context),
):
    return await ops_engine.copilot_query(request, tenant_id=context.tenant_id)


@router.get("/metrics", response_model=OpsMetricsSnapshot)
def metrics(
    context: TenantContext = Depends(get_tenant_context),
):
    return ops_engine.metrics(context.tenant_id)


@router.get("/runtime")
def runtime_settings(
    context: TenantContext = Depends(get_tenant_context),
):
    settings = get_settings()
    mode = settings.normalized_app_mode()
    return {
        "tenant_id": context.tenant_id,
        "mode": mode,
        "features": {
            "synthetic_seed_enabled": mode == "demo",
            "demo_flow_enabled": mode == "demo",
            "autonomy_enabled": True,
            "strict_samsara_sync": True,
            "free_roam_enabled": settings.free_roam_enabled,
            "free_roam_ready": ops_engine.free_roam_ready(),
        },
    }


@router.post("/seed/synthetic")
def seed_synthetic(
    request: SyntheticScenarioRequest,
    context: TenantContext = Depends(require_roles("admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    settings = get_settings()
    if not settings.is_demo_mode():
        raise HTTPException(status_code=403, detail="Synthetic seed is disabled in production mode.")
    cached = _idempotency_lookup(context, "seed_synthetic", idempotency_key)
    if cached:
        return cached
    response = ops_engine.seed_synthetic(
        tenant_id=context.tenant_id,
        seed=request.seed,
        loads=request.loads,
        exception_ratio=request.include_exceptions_ratio,
        actor=context.actor,
    )
    _idempotency_store(context, "seed_synthetic", idempotency_key, response)
    return response


@router.post("/seed/demo-pack", response_model=DemoPackSeedResponse)
async def seed_demo_pack(
    request: DemoPackSeedRequest,
    context: TenantContext = Depends(require_roles("admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    settings = get_settings()
    if not settings.is_demo_mode():
        raise HTTPException(status_code=403, detail="Demo pack seed is disabled in production mode.")
    cached = _idempotency_lookup(context, "seed_demo_pack", idempotency_key)
    if cached:
        return cached
    response = await ops_engine.seed_demo_pack(
        tenant_id=context.tenant_id,
        request=request,
        actor=context.actor,
    )
    _idempotency_store(context, "seed_demo_pack", idempotency_key, response)
    return response


@router.post("/autonomy/run", response_model=AutonomyRunResponse)
async def run_autonomy_cycle(
    request: AutonomyRunRequest,
    context: TenantContext = Depends(require_roles("admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    cached = _idempotency_lookup(context, "autonomy_run", idempotency_key)
    if cached:
        return cached
    response = await ops_engine.run_autonomy_cycle(request, tenant_id=context.tenant_id, actor=context.actor)
    payload = response.model_dump(mode="json")
    _idempotency_store(context, "autonomy_run", idempotency_key, payload)
    return response
