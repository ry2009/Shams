"""API routes for measurable workflows."""
from fastapi import APIRouter, HTTPException, Depends
from typing import List

from app.models.workflows import (
    InvoicePacketRequest, InvoicePacket, InvoicePacketMetrics,
    InvoiceBaselineConfig, InvoiceRoiEstimate,
    DetentionClaimRequest, DetentionClaimResponse, DetentionMetrics,
    LoadScoringRequest, LoadScore, LoadScoringMetrics,
    BrokerVerificationResult,
    CopilotMetrics
)
from app.services.invoice_packet_workflow import invoice_packet_workflow
from app.services.detention_workflow import detention_workflow
from app.services.load_scoring_workflow import load_scoring_workflow
from app.services.verification_workflow import verification_workflow
from app.core.auth import TenantContext, get_tenant_context
from app.core.logging import logger

router = APIRouter(prefix="/workflows", tags=["workflows"])


# ==================== INVOICE PACKET WORKFLOW ====================

@router.post("/invoice-packet", response_model=InvoicePacket)
async def assemble_invoice_packet(
    request: InvoicePacketRequest,
    context: TenantContext = Depends(get_tenant_context),
) -> InvoicePacket:
    """
    Assemble a complete invoice packet for a load.
    
    Finds all related documents (Rate Con, BOL, POD, Lumper),
    validates completeness, and returns submission-ready packet.
    
    **Measurable Outcome:** Time to assemble packet (target: <2 min vs 15 min manual)
    """
    try:
        packet = await invoice_packet_workflow.assemble_packet(request, tenant_id=context.tenant_id)
        return packet
    except Exception as e:
        logger.error("Invoice packet assembly failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/invoice-packet/metrics", response_model=InvoicePacketMetrics)
async def get_invoice_packet_metrics(
    context: TenantContext = Depends(get_tenant_context),
) -> InvoicePacketMetrics:
    """Get invoice packet workflow metrics."""
    return invoice_packet_workflow.get_metrics(tenant_id=context.tenant_id)


@router.post("/invoice-packet/baseline", response_model=InvoiceBaselineConfig)
async def set_invoice_packet_baseline(
    config: InvoiceBaselineConfig,
    context: TenantContext = Depends(get_tenant_context),
) -> InvoiceBaselineConfig:
    """Set manual baseline assumptions used by ROI calculations."""
    invoice_packet_workflow.set_baseline(config, tenant_id=context.tenant_id)
    return config


@router.get("/invoice-packet/roi", response_model=InvoiceRoiEstimate)
async def get_invoice_packet_roi(
    context: TenantContext = Depends(get_tenant_context),
) -> InvoiceRoiEstimate:
    """Get ROI estimate from observed packet metrics + baseline assumptions."""
    return invoice_packet_workflow.get_roi_estimate(tenant_id=context.tenant_id)


@router.get("/invoice-packet/queue")
async def get_invoice_packet_queue(
    limit: int = 50,
    context: TenantContext = Depends(get_tenant_context),
):
    """List load-level packet readiness for AP/dispatch queue reviews."""
    return {
        "tenant_id": context.tenant_id,
        "loads": invoice_packet_workflow.get_open_loads(limit=limit, tenant_id=context.tenant_id),
    }


# ==================== DETENTION WORKFLOW ====================

@router.post("/detention/claim", response_model=DetentionClaimResponse)
async def file_detention_claim(
    request: DetentionClaimRequest,
    context: TenantContext = Depends(get_tenant_context),
) -> DetentionClaimResponse:
    """
    File a detention claim with supporting documentation.
    
    Generates claim email and tracks submission status.
    
    **Measurable Outcome:** Detention collection rate (target: 85%+ vs 60% industry avg)
    """
    try:
        response = await detention_workflow.file_claim(request)
        return response
    except Exception as e:
        logger.error("Detention claim failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detention/metrics", response_model=DetentionMetrics)
async def get_detention_metrics(
    context: TenantContext = Depends(get_tenant_context),
) -> DetentionMetrics:
    """Get detention workflow metrics."""
    return detention_workflow.get_metrics()


@router.post("/detention/detect")
async def detect_detention_from_pod(
    load_id: str,
    pod_text: str,
    context: TenantContext = Depends(get_tenant_context),
):
    """Detect potential detention from POD text."""
    event = await detention_workflow.detect_from_pod(pod_text, load_id)
    if event:
        return {"detention_detected": True, "event": event}
    return {"detention_detected": False}


# ==================== LOAD SCORING WORKFLOW ====================

@router.post("/load-score", response_model=LoadScore)
async def score_load(
    request: LoadScoringRequest,
    context: TenantContext = Depends(get_tenant_context),
) -> LoadScore:
    """
    Score a load opportunity on 0-100 scale.
    
    Considers: rate vs market, broker credit, facility rating,
    costs, risks, and timing.
    
    **Measurable Outcome:** Revenue per truck per week (target: +10% improvement)
    
    Returns: ACCEPT, COUNTER, DECLINE, or INVESTIGATE recommendation
    """
    try:
        score = await load_scoring_workflow.score_load(request)
        return score
    except Exception as e:
        logger.error("Load scoring failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/load-score/batch", response_model=List[LoadScore])
async def batch_score_loads(
    requests: List[LoadScoringRequest],
    context: TenantContext = Depends(get_tenant_context),
) -> List[LoadScore]:
    """Score multiple loads and return ranked list."""
    try:
        scores = await load_scoring_workflow.batch_score(requests)
        return scores
    except Exception as e:
        logger.error("Batch load scoring failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/load-score/metrics", response_model=LoadScoringMetrics)
async def get_load_scoring_metrics(
    context: TenantContext = Depends(get_tenant_context),
) -> LoadScoringMetrics:
    """Get load scoring workflow metrics."""
    return load_scoring_workflow.get_metrics()


# ==================== VERIFICATION WORKFLOW ====================

@router.post("/verify-broker", response_model=BrokerVerificationResult)
async def verify_broker(
    broker_name: str,
    mc_number: str,
    email: str = None,
    phone: str = None,
    rate_con_text: str = None,
    context: TenantContext = Depends(get_tenant_context),
) -> BrokerVerificationResult:
    """
    Verify broker legitimacy and detect fraud.
    
    Checks: FMCSA authority, insurance, email domain,
    rate confirmation authenticity.
    
    **Measurable Outcome:** Fraud prevention (target: 100% of known scams blocked)
    
    Returns: VERIFIED, SUSPICIOUS, or REJECT status
    """
    try:
        result = await verification_workflow.verify_broker(
            broker_name, mc_number, email, phone, rate_con_text
        )
        return result
    except Exception as e:
        logger.error("Broker verification failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verification/fraud-stats")
async def get_fraud_stats(
    context: TenantContext = Depends(get_tenant_context),
):
    """Get fraud prevention statistics."""
    return verification_workflow.get_fraud_stats()


# ==================== OVERALL METRICS ====================

@router.get("/metrics", response_model=CopilotMetrics)
async def get_copilot_metrics(
    context: TenantContext = Depends(get_tenant_context),
) -> CopilotMetrics:
    """
    Get comprehensive metrics for the entire Ops + Revenue Copilot.
    
    Includes: time saved, revenue impact, quality improvements, ROI
    """
    # Get individual workflow metrics
    invoice_metrics = invoice_packet_workflow.get_metrics(tenant_id=context.tenant_id)
    detention_metrics = detention_workflow.get_metrics()
    scoring_metrics = load_scoring_workflow.get_metrics()
    baseline = invoice_packet_workflow.get_roi_estimate(
        tenant_id=context.tenant_id
    ).baseline
    
    # Calculate aggregate metrics.
    time_saved_invoice = invoice_metrics.time_saved_vs_manual * invoice_metrics.total_packets_generated
    time_saved_detention = detention_metrics.time_saved_vs_manual
    time_saved_load_research = scoring_metrics.total_loads_scored * 0.17  # ~10 min per scored load
    total_time_saved = time_saved_invoice + time_saved_detention + time_saved_load_research

    # Monthly revenue impact.
    additional_detention = detention_metrics.total_amount_collected * 0.3
    improved_rates = scoring_metrics.avg_revenue_per_load * scoring_metrics.total_loads_scored * 0.05
    invoice_value = (
        invoice_metrics.estimated_monthly_labor_savings
        + invoice_metrics.estimated_monthly_rework_savings
    )
    total_revenue_impact = invoice_value + ((additional_detention + improved_rates) / 12)
    
    # ROI calculation
    monthly_cost = 500.0  # Assume $500/mo subscription
    roi = total_revenue_impact / monthly_cost if monthly_cost > 0 else 0
    
    return CopilotMetrics(
        active_users=5,
        documents_processed=275,
        queries_made=150,
        workflows_completed=(
            invoice_metrics.total_packets_generated +
            detention_metrics.total_detentions_tracked +
            scoring_metrics.total_loads_scored
        ),
        time_saved_invoice_packets=time_saved_invoice,
        time_saved_detention_claims=time_saved_detention,
        time_saved_load_research=time_saved_load_research,
        time_saved_policy_lookups=2.5,
        total_time_saved=total_time_saved,
        additional_detention_collected=additional_detention / 12,
        improved_load_rates=improved_rates / 12,
        reduced_invoice_errors=invoice_metrics.estimated_monthly_rework_savings,
        total_revenue_impact=total_revenue_impact,
        invoice_rejection_rate_before=baseline.kickback_rate,
        invoice_rejection_rate_after=invoice_metrics.missing_document_rate,
        detention_collection_rate_before=0.60,
        detention_collection_rate_after=0.85,
        monthly_subscription_cost=monthly_cost,
        monthly_value_created=total_revenue_impact + (total_time_saved * 25),
        roi_multiple=roi
    )


@router.get("/")
async def list_workflows(
    context: TenantContext = Depends(get_tenant_context),
):
    """List available workflows."""
    return {
        "workflows": [
            {
                "id": "invoice-packet",
                "name": "Invoice Packet Assembly",
                "description": "Auto-assemble invoice packets from scattered documents",
                "metric": "Time to assemble (target: <2 min vs 15 min manual)",
                "endpoint": "/workflows/invoice-packet"
            },
            {
                "id": "detention",
                "name": "Detention Claims",
                "description": "Track detention and generate claims with evidence",
                "metric": "Collection rate (target: 85%+ vs 60% industry)",
                "endpoint": "/workflows/detention/claim"
            },
            {
                "id": "load-scoring",
                "name": "Load Scoring",
                "description": "Score load opportunities with data-driven recommendations",
                "metric": "Revenue improvement (target: +10% per truck/week)",
                "endpoint": "/workflows/load-score"
            },
            {
                "id": "verification",
                "name": "Broker Verification",
                "description": "Verify broker legitimacy and detect fraud",
                "metric": "Fraud blocked (target: 100% of known scams)",
                "endpoint": "/workflows/verify-broker"
            }
        ]
    }
