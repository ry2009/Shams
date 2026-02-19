"""Strict telemetry adapter endpoints consumed by SHAMS Samsara sync."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.core.logging import logger
from app.models.ops import SamsaraAdapterIngestRequest, SamsaraAdapterQueryRequest
from app.services.ops_state import ops_state_store


router = APIRouter(prefix="/samsara-adapter", tags=["samsara-adapter"])


def _authorize_adapter(authorization: str | None) -> None:
    settings = get_settings()
    expected = (settings.samsara_api_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Samsara adapter is disabled: SAMSARA_API_TOKEN is not configured.",
        )

    auth_header = (authorization or "").strip()
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
    received = auth_header.split(" ", 1)[1].strip()
    if received != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid adapter token.")


@router.post("/events/ingest")
def ingest_events(
    request: SamsaraAdapterIngestRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    _authorize_adapter(authorization)
    result = ops_state_store.ingest_samsara_events(request.tenant_id, [row.model_dump(mode="json") for row in request.events])
    logger.info(
        "Samsara adapter ingested events",
        tenant_id=request.tenant_id,
        ingested=result["ingested"],
        skipped=result["skipped"],
    )
    return {"tenant_id": request.tenant_id, **result}


@router.post("/events/query")
def query_events(
    request: SamsaraAdapterQueryRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    _authorize_adapter(authorization)
    events = ops_state_store.query_samsara_events(
        tenant_id=request.tenant_id,
        load_ids=request.load_ids,
        hours_back=request.hours_back,
    )
    return {"tenant_id": request.tenant_id, "events": events}
