"""API routes for RAG queries."""
from fastapi import APIRouter, HTTPException, Depends

from app.models.document import QueryRequest, QueryResponse
from app.services.rag_engine import rag_engine
from app.core.auth import TenantContext, get_tenant_context
from app.core.logging import logger

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/query", response_model=QueryResponse)
async def query_documents(
    request: QueryRequest,
    context: TenantContext = Depends(get_tenant_context),
) -> QueryResponse:
    """Query documents using RAG."""
    try:
        response = await rag_engine.query(request, tenant_id=context.tenant_id)
        return response
    except Exception as e:
        logger.error("RAG query endpoint failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/quick-ask")
async def quick_ask(
    query: str,
    context: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Quick ask endpoint for simple queries."""
    request = QueryRequest(
        query=query,
        include_sources=True
    )
    
    try:
        response = await rag_engine.query(request, tenant_id=context.tenant_id)
        return {
            "answer": response.answer,
            "sources": [s["filename"] for s in response.sources],
            "confidence": response.confidence,
            "tenant_id": context.tenant_id,
        }
    except Exception as e:
        logger.error("Quick ask failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def rag_health() -> dict:
    """Check RAG system health."""
    return {
        "status": "healthy",
        "engine": "RAG v1",
        "runtime": rag_engine.get_runtime_info(),
    }


@router.get("/metrics")
async def rag_metrics() -> dict:
    """Get rolling RAG latency and route metrics."""
    return rag_engine.get_latency_metrics()
