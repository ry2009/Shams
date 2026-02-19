"""API routes for rate negotiation assistance."""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional

from app.services.rag_engine import rag_engine
from app.core.auth import TenantContext, get_tenant_context
from app.core.logging import logger

router = APIRouter(prefix="/negotiation", tags=["negotiation"])


class CounterOfferRequest(BaseModel):
    """Request to generate a counter-offer."""
    load_number: Optional[str] = None
    pickup_location: str
    delivery_location: str
    pickup_date: str
    delivery_date: str
    equipment_type: str
    current_rate: float
    target_rate: float
    miles: Optional[float] = None
    reasoning: str = Field(..., description="Why you're asking for more (market rates, deadhead, etc.)")
    broker_name: Optional[str] = None


class CounterOfferResponse(BaseModel):
    """Generated counter-offer email."""
    email_subject: str
    email_body: str
    rate_per_mile_current: Optional[float]
    rate_per_mile_target: float


@router.post("/counter-offer", response_model=CounterOfferResponse)
async def generate_counter_offer(
    request: CounterOfferRequest,
    context: TenantContext = Depends(get_tenant_context),
) -> CounterOfferResponse:
    """Generate a professional counter-offer email."""
    
    try:
        rate_con_data = {
            "load_number": request.load_number,
            "pickup_location": request.pickup_location,
            "delivery_location": request.delivery_location,
            "pickup_date": request.pickup_date,
            "delivery_date": request.delivery_date,
            "equipment_type": request.equipment_type,
            "rate": request.current_rate,
            "miles": request.miles,
            "broker_name": request.broker_name
        }
        
        email_body = await rag_engine.generate_counter_offer(
            rate_con_data=rate_con_data,
            target_rate=request.target_rate,
            reasoning=request.reasoning
        )
        
        # Generate subject line
        load_ref = request.load_number or "Load"
        email_subject = f"Counter Offer - {load_ref} - ${request.target_rate:,.2f}"
        
        # Calculate RPM
        rpm_current = request.current_rate / request.miles if request.miles else None
        rpm_target = request.target_rate / request.miles if request.miles else None
        
        return CounterOfferResponse(
            email_subject=email_subject,
            email_body=email_body,
            rate_per_mile_current=round(rpm_current, 2) if rpm_current else None,
            rate_per_mile_target=round(rpm_target, 2) if rpm_target else None
        )
        
    except Exception as e:
        logger.error("Counter offer generation failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


class RateAnalysisRequest(BaseModel):
    """Request to analyze if a rate is good."""
    origin: str
    destination: str
    rate: float
    miles: float
    equipment_type: str
    market_rate_low: Optional[float] = None
    market_rate_high: Optional[float] = None


class RateAnalysisResponse(BaseModel):
    """Rate analysis result."""
    rate_per_mile: float
    market_rate_per_mile_low: Optional[float]
    market_rate_per_mile_high: Optional[float]
    verdict: str  # "good", "fair", "poor"
    reasoning: str
    suggested_counter: Optional[float]


@router.post("/analyze-rate", response_model=RateAnalysisResponse)
async def analyze_rate(
    request: RateAnalysisRequest,
    context: TenantContext = Depends(get_tenant_context),
) -> RateAnalysisResponse:
    """Analyze if a rate is competitive and suggest counter if needed."""
    
    rpm = request.rate / request.miles
    
    # Simple logic for now - can be enhanced with market data
    market_low = request.market_rate_low or (rpm * 0.9)
    market_high = request.market_rate_high or (rpm * 1.2)
    
    if rpm >= market_high * 0.95:
        verdict = "good"
        reasoning = "This rate is at or above market rates for this lane."
        suggested_counter = None
    elif rpm >= market_low:
        verdict = "fair"
        reasoning = "This rate is within market range but there's room to negotiate."
        suggested_counter = request.rate * 1.1
    else:
        verdict = "poor"
        reasoning = "This rate is below market. Consider negotiating or finding alternative freight."
        suggested_counter = request.rate * 1.15
    
    return RateAnalysisResponse(
        rate_per_mile=round(rpm, 2),
        market_rate_per_mile_low=round(market_low, 2) if request.market_rate_low else None,
        market_rate_per_mile_high=round(market_high, 2) if request.market_rate_high else None,
        verdict=verdict,
        reasoning=reasoning,
        suggested_counter=round(suggested_counter, 2) if suggested_counter else None
    )
