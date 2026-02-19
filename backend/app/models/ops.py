"""Domain models for SHAMS autonomous dispatch, ticketing, and billing workflows."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, Enum):
    """Supported UI/API operator roles."""

    DISPATCHER = "dispatcher"
    BILLING = "billing"
    ADMIN = "admin"


class LoadStatus(str, Enum):
    """Operational lifecycle status for a load."""

    PLANNED = "planned"
    ASSIGNED = "assigned"
    EN_ROUTE = "en_route"
    DELIVERED = "delivered"
    BLOCKED = "blocked"


class TicketStatus(str, Enum):
    """Review lifecycle status for ticket workflows."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    EXCEPTION = "exception"
    RESOLVED = "resolved"


class LoadCreateRequest(BaseModel):
    """Request payload to create a new load in dispatch."""

    load_id: Optional[str] = None
    customer: str
    broker: Optional[str] = None
    pickup_location: str
    delivery_location: str
    pickup_time: Optional[str] = None
    delivery_time: Optional[str] = None
    equipment_type: str = "flatbed"
    planned_miles: float = Field(default=0.0, ge=0)
    rate_total: float = Field(default=0.0, ge=0)
    zone: Optional[str] = None
    priority: str = "normal"
    notes: Optional[str] = None
    source: str = "manual"


class LoadUpdateRequest(BaseModel):
    """Patch fields for an existing load."""

    customer: Optional[str] = None
    broker: Optional[str] = None
    pickup_location: Optional[str] = None
    delivery_location: Optional[str] = None
    pickup_time: Optional[str] = None
    delivery_time: Optional[str] = None
    equipment_type: Optional[str] = None
    planned_miles: Optional[float] = Field(default=None, ge=0)
    rate_total: Optional[float] = Field(default=None, ge=0)
    zone: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[LoadStatus] = None
    expected_version: Optional[int] = Field(default=None, ge=1)


class LoadAssignmentRequest(BaseModel):
    """Assignment payload for driver/truck resources."""

    load_id: str
    driver_id: Optional[str] = None
    truck_id: Optional[str] = None
    trailer_id: Optional[str] = None
    dispatcher_id: Optional[str] = None
    auto: bool = True


class LoadRecord(BaseModel):
    """Persisted load record."""

    load_id: str
    customer: str
    broker: Optional[str] = None
    pickup_location: str
    delivery_location: str
    pickup_time: Optional[str] = None
    delivery_time: Optional[str] = None
    equipment_type: str = "flatbed"
    planned_miles: float = 0.0
    rate_total: float = 0.0
    zone: Optional[str] = None
    priority: str = "normal"
    notes: Optional[str] = None
    source: str = "manual"
    status: LoadStatus = LoadStatus.PLANNED
    assignment: Dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TimelineEvent(BaseModel):
    """Operational event associated with a load."""

    event_id: str
    load_id: str
    event_type: str
    actor: str
    timestamp: datetime = Field(default_factory=_utcnow)
    details: Dict[str, Any] = Field(default_factory=dict)


class DispatchBoardResponse(BaseModel):
    """Dispatch board data by status and unassigned flags."""

    counts_by_status: Dict[str, int]
    loads: List[LoadRecord]
    drivers: List[Dict[str, Any]] = Field(default_factory=list)


class LoadStatusTransitionRequest(BaseModel):
    """Request to move a load through lifecycle states."""

    status: LoadStatus
    expected_version: Optional[int] = Field(default=None, ge=1)


class TicketReviewRequest(BaseModel):
    """Input to run autonomous ticket review for a load."""

    load_id: str
    ticket_number: Optional[str] = None
    gps_miles: Optional[float] = Field(default=None, ge=0)
    rated_miles: Optional[float] = Field(default=None, ge=0)
    zone: Optional[str] = None
    expected_rate: Optional[float] = Field(default=None, ge=0)
    gps_hours_back: int = Field(default=72, ge=1, le=336)
    document_ids: List[str] = Field(default_factory=list)
    force_recompute: bool = False


class ConfidenceField(BaseModel):
    """Confidence score for one extracted field."""

    field: str
    value: Any = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    source: str = "unknown"


class RuleSeverity(str, Enum):
    """Rule severity levels."""

    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class RuleResult(BaseModel):
    """One deterministic validation rule output."""

    rule_id: str
    description: str
    severity: RuleSeverity
    passed: bool
    evidence: Dict[str, Any] = Field(default_factory=dict)
    suggestion: Optional[str] = None


class TicketReviewResult(BaseModel):
    """Autonomous ticket review output."""

    review_id: str
    load_id: str
    ticket_number: Optional[str] = None
    status: TicketStatus
    auto_approved: bool
    approval_reason: str
    final_confidence: float = Field(default=0.0, ge=0, le=1)
    confidence_profile: List[ConfidenceField]
    rules: List[RuleResult]
    failed_rules: List[str] = Field(default_factory=list)
    leakage_findings: List[str] = Field(default_factory=list)
    billing_ready: bool = False
    processing_time_ms: float = 0.0
    documents_used: List[str] = Field(default_factory=list)
    missing_documents: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


class TicketDecisionRequest(BaseModel):
    """Manual override for exception tickets."""

    decision: str = Field(description="approve|reject|resolve")
    note: Optional[str] = None


class BillingReadinessRecord(BaseModel):
    """Billing readiness summary per load."""

    load_id: str
    status: str
    billing_ready: bool
    ready_reason: str
    required_documents: List[str] = Field(default_factory=list)
    missing_documents: List[str] = Field(default_factory=list)
    leakage_findings: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_utcnow)


class McleodExportResponse(BaseModel):
    """Export artifact response for temporary McLeod bridge."""

    export_id: str
    load_id: str
    status: str
    artifact_path: str
    generated_at: datetime = Field(default_factory=_utcnow)


class CopilotQueryRequest(BaseModel):
    """Context-aware copilot query request."""

    query: str
    load_id: Optional[str] = None
    mode: str = "auto"  # auto|deterministic|free_roam
    session_id: Optional[str] = "atlas"


class CopilotQueryResponse(BaseModel):
    """Context-aware copilot query response."""

    answer: str
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    processing_time_ms: float = 0.0
    route: str = "deterministic"
    actions: List[Dict[str, Any]] = Field(default_factory=list)


class OpsMetricsSnapshot(BaseModel):
    """Topline KPI snapshot for MVP reporting."""

    active_loads: int
    delivered_loads: int
    auto_assignment_rate: float
    tickets_reviewed: int
    auto_approval_rate: float
    exception_rate: float
    billing_ready_rate: float
    estimated_leakage_recovered_usd: float
    avg_review_latency_ms: float
    p95_review_latency_ms: float


class SamsaraSyncRequest(BaseModel):
    """Read-only sync request for trip telemetry."""

    load_ids: List[str] = Field(default_factory=list)
    hours_back: int = Field(default=24, ge=1, le=168)


class SamsaraAdapterEvent(BaseModel):
    """Canonical telemetry event used by the SHAMS Samsara adapter."""

    load_id: str
    gps_miles: float = Field(ge=0)
    stop_events: int = Field(default=0, ge=0)
    vehicle_id: Optional[str] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    event_time: Optional[str] = None


class SamsaraAdapterIngestRequest(BaseModel):
    """Batch ingest request for telemetry events."""

    tenant_id: str
    events: List[SamsaraAdapterEvent]


class SamsaraAdapterQueryRequest(BaseModel):
    """Query request for telemetry events."""

    tenant_id: str
    load_ids: List[str] = Field(default_factory=list)
    hours_back: int = Field(default=24, ge=1, le=168)


class SyntheticScenarioRequest(BaseModel):
    """Generate synthetic loads/tickets for realistic demo workflows."""

    seed: int = 42
    loads: int = Field(default=24, ge=1, le=500)
    include_exceptions_ratio: float = Field(default=0.2, ge=0, le=1)


class DemoPackSeedRequest(BaseModel):
    """Generate a full demo-ready dataset including synthetic documents."""

    seed: int = 42
    loads: int = Field(default=24, ge=1, le=500)
    docs_per_load: int = Field(default=3, ge=1, le=5)
    include_exceptions_ratio: float = Field(default=0.22, ge=0, le=1)
    index_documents: bool = False


class DemoPackSeedResponse(BaseModel):
    """Summary of generated demo dataset assets."""

    loads_created: int
    documents_created: int
    documents_indexed: int
    load_ids: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class AutonomyRunRequest(BaseModel):
    """Run one autonomous cycle across dispatch, ticketing, and billing/export."""

    max_loads: int = Field(default=50, ge=1, le=500)
    include_exports: bool = True


class AutonomyRunResponse(BaseModel):
    """Result summary for one autonomous cycle."""

    scanned_loads: int
    assigned_loads: int
    reviewed_loads: int
    exports_generated: int
    errors: List[str] = Field(default_factory=list)
