"""Models for SHAMS Agent OS autonomous orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRunStatus(str, Enum):
    """Lifecycle state for one agent run."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELED = "canceled"


class AgentStepStatus(str, Enum):
    """Lifecycle state for one step in a run."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentAutonomyLevel(str, Enum):
    """Execution autonomy level."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class AgentExecutionMode(str, Enum):
    """How the agent executes actions."""

    STATE_FIRST = "state_first"
    UI_FIRST = "ui_first"
    HYBRID = "hybrid"


class AgentActionType(str, Enum):
    """Supported action identifiers."""

    DRIVER_ADD = "fleet.add_driver"
    DRIVER_REMOVE = "fleet.remove_driver"
    DISPATCH_ASSIGN = "dispatch.assign_loads"
    TICKET_REVIEW = "tickets.review_pending"
    BILLING_EXPORT = "billing.export_ready"
    SYSTEM_RESET = "system.reset_demo_data"


class AgentPolicyRule(BaseModel):
    """Policy rule evaluated before action execution."""

    policy_id: str
    action_type: AgentActionType
    enabled: bool = True
    requires_admin_approval: bool = False
    destructive: bool = False
    min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    max_targets: int = Field(default=100, ge=1)
    notes: str = ""


class PolicyDecision(BaseModel):
    """Result of a policy check."""

    allowed: bool
    requires_approval: bool = False
    reason: str
    policy_id: Optional[str] = None


class AgentRunRequest(BaseModel):
    """Request to start one autonomous run."""

    objective: str = Field(min_length=3)
    autonomy_level: AgentAutonomyLevel = AgentAutonomyLevel.L3
    execution_mode: AgentExecutionMode = AgentExecutionMode.HYBRID
    dry_run: bool = False
    max_steps: int = Field(default=12, ge=1, le=100)


class AgentStepRecord(BaseModel):
    """Persisted execution step."""

    step_id: str
    run_id: str
    step_index: int = Field(ge=0)
    action_type: AgentActionType
    status: AgentStepStatus
    prompt: str
    policy_decision: PolicyDecision
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    compensation: Dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AgentRunRecord(BaseModel):
    """Persisted execution run."""

    run_id: str
    tenant_id: str
    actor: str
    role: str
    objective: str
    autonomy_level: AgentAutonomyLevel
    execution_mode: AgentExecutionMode
    dry_run: bool = False
    status: AgentRunStatus = AgentRunStatus.PENDING
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    blocked_approval_id: Optional[str] = None
    summary: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AgentApprovalRecord(BaseModel):
    """Approval gate for high-risk actions."""

    approval_id: str
    run_id: str
    step_id: str
    policy_id: str
    status: str = "pending"
    requested_by: str = "agent"
    requested_at: datetime = Field(default_factory=_utcnow)
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    note: str = ""


class AgentRunResponse(BaseModel):
    """Response returned after run creation/resume."""

    run: AgentRunRecord
    steps: List[AgentStepRecord]
    approvals: List[AgentApprovalRecord] = Field(default_factory=list)


class AgentApprovalDecisionRequest(BaseModel):
    """Approval decision payload."""

    approval_id: str
    approve: bool = True
    note: str = ""


class AgentPolicyPatchRequest(BaseModel):
    """Patch policy rule fields."""

    enabled: Optional[bool] = None
    requires_admin_approval: Optional[bool] = None
    destructive: Optional[bool] = None
    min_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_targets: Optional[int] = Field(default=None, ge=1)
    notes: Optional[str] = None


class AgentRunMetrics(BaseModel):
    """Aggregate metrics for Agent OS runs."""

    runs_total: int
    runs_completed: int
    runs_waiting_approval: int
    runs_failed: int
    step_success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    p95_step_latency_ms: float = Field(default=0.0, ge=0.0)
