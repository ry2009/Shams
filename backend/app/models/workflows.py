"""Workflow-specific models for measurable trucking operations."""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional, Dict, Any
from enum import Enum


class WorkflowType(str, Enum):
    INVOICE_PACKET = "invoice_packet"
    DETENTION_CLAIM = "detention_claim"
    LOAD_SCORING = "load_scoring"
    BROKER_VERIFICATION = "broker_verification"
    RATE_NEGOTIATION = "rate_negotiation"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ERROR = "error"
    NEEDS_REVIEW = "needs_review"


# ==================== INVOICE PACKET WORKFLOW ====================

class InvoicePacketRequest(BaseModel):
    """Request to assemble an invoice packet."""
    load_id: str
    rate_confirmation_id: Optional[str] = None
    document_ids: List[str] = Field(default_factory=list)
    auto_find_documents: bool = True


class DocumentMatch(BaseModel):
    """A matched document for invoice packet."""
    document_id: str
    document_type: str
    filename: str
    confidence: float
    matched_field: str  # What field matched (load_id, bol_number, pro_number, etc.)


class InvoicePacket(BaseModel):
    """Complete invoice packet ready for submission."""
    load_id: str
    status: WorkflowStatus
    
    # Required components
    rate_confirmation: Optional[DocumentMatch] = None
    invoice: Optional[DocumentMatch] = None
    bol: Optional[DocumentMatch] = None
    pod: Optional[DocumentMatch] = None
    lumper_receipt: Optional[DocumentMatch] = None
    
    # Extracted data
    broker_name: Optional[str] = None
    broker_mc: Optional[str] = None
    invoice_amount: Optional[float] = None
    load_details: Dict[str, Any] = Field(default_factory=dict)
    
    # Validation
    missing_documents: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    next_actions: List[str] = Field(default_factory=list)
    
    # Audit
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    generated_by: str = "system"
    packet_id: str = Field(default_factory=lambda: f"PKT{datetime.now().strftime('%Y%m%d%H%M%S')}")


class InvoicePacketMetrics(BaseModel):
    """Metrics for invoice packet workflow."""
    total_packets_generated: int
    average_time_seconds: float
    missing_document_rate: float
    rejection_rate: float  # Invoices kicked back by brokers
    time_saved_vs_manual: float  # Hours per week
    manual_average_minutes: float = 15.0
    automated_average_minutes: float = 0.0
    estimated_monthly_labor_savings: float = 0.0
    estimated_monthly_rework_savings: float = 0.0


class InvoiceBaselineConfig(BaseModel):
    """Baseline assumptions used for ROI calculations."""
    avg_manual_minutes_per_invoice: float = Field(..., gt=0)
    monthly_invoice_volume: int = Field(..., gt=0)
    kickback_rate: float = Field(..., ge=0, le=1)
    avg_rework_minutes: float = Field(default=20.0, ge=0)
    labor_rate_per_hour: float = Field(default=24.44, gt=0)


class InvoiceRoiEstimate(BaseModel):
    """Estimated ROI output for invoice packet workflow."""
    baseline: InvoiceBaselineConfig
    observed_average_seconds: float
    observed_missing_document_rate: float
    minutes_saved_per_invoice: float
    monthly_labor_savings: float
    monthly_kickbacks_avoided: float
    monthly_rework_savings: float
    total_monthly_value: float


# ==================== DETENTION CLAIM WORKFLOW ====================

class DetentionEvent(BaseModel):
    """A single detention event."""
    event_id: str
    load_id: str
    facility_name: str
    arrival_time: datetime
    check_in_time: datetime
    unloaded_time: datetime
    
    # Calculated
    total_hours: float
    free_time_hours: float = 2.0
    billable_hours: float
    
    # Financial
    rate_per_hour: float = 50.0
    total_amount: float
    
    # Evidence
    evidence_documents: List[str] = Field(default_factory=list)
    has_signed_authorization: bool = False
    authorization_document_id: Optional[str] = None
    
    # Claim status
    status: WorkflowStatus = WorkflowStatus.PENDING
    submitted_to_broker: Optional[datetime] = None
    broker_response: Optional[str] = None
    paid_amount: Optional[float] = None


class DetentionClaimRequest(BaseModel):
    """Request to file a detention claim."""
    load_id: str
    facility_name: str
    arrival_time: datetime
    unloaded_time: datetime
    rate_per_hour: float = 50.0
    free_time_hours: float = 2.0
    supporting_document_ids: List[str] = Field(default_factory=list)


class DetentionClaimResponse(BaseModel):
    """Response with generated claim."""
    event: DetentionEvent
    claim_email_draft: str
    supporting_evidence: List[Dict[str, Any]]
    expected_amount: float
    success_probability: float  # Based on facility/broker history


class DetentionMetrics(BaseModel):
    """Metrics for detention workflow."""
    total_detentions_tracked: int
    total_amount_claimed: float
    total_amount_collected: float
    collection_rate: float
    average_response_time_days: float
    time_saved_vs_manual: float  # Hours per week


# ==================== LOAD SCORING WORKFLOW ====================

class LoadScoreFactors(BaseModel):
    """Factors that contribute to load score."""
    # Revenue factors (0-100)
    rate_per_mile_score: int = 50  # Above/below market rate
    total_revenue_score: int = 50  # Absolute dollar amount
    
    # Cost factors (0-100, higher is better)
    deadhead_score: int = 50  # Distance to pickup
    fuel_cost_score: int = 50  # Based on mileage
    
    # Operational factors (0-100)
    facility_rating: int = 50  # Based on detention history
    broker_rating: int = 50  # Credit score + payment history
    equipment_match: int = 100  # 100 if exact match
    
    # Time factors (0-100)
    pickup_window_score: int = 50  # How flexible is pickup
    delivery_window_score: int = 50
    
    # Risk factors (0-100, higher = less risky)
    fraud_risk_score: int = 100
    weather_risk_score: int = 100
    market_volatility_score: int = 50


class LoadScore(BaseModel):
    """Score for a load opportunity."""
    load_id: str
    broker_name: str
    origin: str
    destination: str
    rate: float
    miles: int
    rpm: float
    
    # Overall score (0-100)
    total_score: int
    recommendation: str  # "ACCEPT", "COUNTER", "DECLINE", "INVESTIGATE"
    
    # Component scores
    factors: LoadScoreFactors
    
    # Benchmarks
    market_rate_low: float
    market_rate_high: float
    market_rate_avg: float
    
    # Reasoning
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    opportunities: List[str] = Field(default_factory=list)


class LoadScoringRequest(BaseModel):
    """Request to score a load."""
    origin: str
    destination: str
    rate: float
    miles: int
    equipment_type: str
    pickup_date: datetime
    broker_mc: Optional[str] = None
    broker_name: Optional[str] = None


class LoadScoringMetrics(BaseModel):
    """Metrics for load scoring workflow."""
    total_loads_scored: int
    average_score: float
    acceptance_rate: float
    counter_rate: float
    decline_rate: float
    avg_revenue_per_load: float
    revenue_improvement_vs_baseline: float  # %


# ==================== BROKER VERIFICATION WORKFLOW ====================

class BrokerVerificationResult(BaseModel):
    """Result of broker verification check."""
    broker_name: str
    mc_number: str
    
    # FMCSA data
    fmscs_authority_status: str  # ACTIVE, INACTIVE, etc.
    insurance_on_file: bool
    insurance_expiration: Optional[datetime] = None
    safety_rating: Optional[str] = None
    
    # Credit/internal data
    credit_score: Optional[int] = None  # 0-100
    days_to_pay: Optional[int] = None
    quickpay_available: bool = False
    
    # Fraud checks
    email_domain_match: bool = True
    email_suspicious: bool = False
    rate_confirmation_authentic: bool = True
    
    # Overall
    verification_status: str  # "VERIFIED", "SUSPICIOUS", "REJECT"
    risk_level: str  # "LOW", "MEDIUM", "HIGH"
    warnings: List[str] = Field(default_factory=list)
    
    # Evidence
    verification_timestamp: datetime = Field(default_factory=datetime.utcnow)
    evidence_links: List[str] = Field(default_factory=list)


# ==================== RATE NEGOTIATION WORKFLOW ====================

class MarketRateData(BaseModel):
    """Market rate data for a lane."""
    origin: str
    destination: str
    equipment_type: str
    
    # Rates
    low_rate: float
    avg_rate: float
    high_rate: float
    target_rate: float  # 75th percentile
    
    # Historical
    rate_trend: str  # "UP", "DOWN", "STABLE"
    volume_trend: str  # "UP", "DOWN", "STABLE"
    
    # Timing
    last_updated: datetime
    data_points: int


class NegotiationStrategy(BaseModel):
    """Strategy for rate negotiation."""
    load_id: str
    current_rate: float
    target_rate: float
    strategy: str  # "HOLD", "COUNTER", "WALK"
    
    # Reasoning
    market_position: str  # "BELOW_MARKET", "AT_MARKET", "ABOVE_MARKET"
    urgency_factors: List[str] = Field(default_factory=list)
    leverage_factors: List[str] = Field(default_factory=list)
    
    # Counter offer
    suggested_counter: float
    minimum_acceptable: float
    justification: str
    
    # Email draft
    email_subject: str
    email_body: str
    
    # Confidence
    success_probability: float
    expected_outcome: str


# ==================== OVERALL METRICS ====================

class CopilotMetrics(BaseModel):
    """Overall metrics for the Ops + Revenue Copilot."""
    
    # Usage
    active_users: int
    documents_processed: int
    queries_made: int
    workflows_completed: int
    
    # Time savings (hours per week)
    time_saved_invoice_packets: float
    time_saved_detention_claims: float
    time_saved_load_research: float
    time_saved_policy_lookups: float
    total_time_saved: float
    
    # Revenue impact ($ per month)
    additional_detention_collected: float
    improved_load_rates: float
    reduced_invoice_errors: float
    total_revenue_impact: float
    
    # Quality improvements
    invoice_rejection_rate_before: float
    invoice_rejection_rate_after: float
    detention_collection_rate_before: float
    detention_collection_rate_after: float
    
    # ROI
    monthly_subscription_cost: float
    monthly_value_created: float
    roi_multiple: float  # Value / Cost
