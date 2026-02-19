"""Pydantic models for documents and RAG operations."""
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class DocumentType(str, Enum):
    """Standard trucking document types."""
    RATE_CONFIRMATION = "rate_confirmation"
    INVOICE = "invoice"
    POD = "proof_of_delivery"  # Proof of Delivery
    BOL = "bill_of_lading"
    LUMPER_RECEIPT = "lumper_receipt"
    EMAIL = "email"
    ROUTING_GUIDE = "routing_guide"
    INSURANCE_CERT = "insurance_certificate"
    SOP = "standard_operating_procedure"
    POLICY = "policy"
    OTHER = "other"


class DocumentStatus(str, Enum):
    """Processing status for documents."""
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    ERROR = "error"


class DocumentChunk(BaseModel):
    """A chunk of a document with metadata."""
    chunk_id: str
    document_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] | None = None


class Document(BaseModel):
    """A trucking document."""
    id: str | None = None
    filename: str
    document_type: DocumentType = DocumentType.OTHER
    status: DocumentStatus = DocumentStatus.PENDING
    raw_text: str = ""
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    processed_at: datetime | None = None


class RateConfirmationData(BaseModel):
    """Extracted data from a rate confirmation."""
    load_number: str | None = None
    broker_name: str | None = None
    broker_mc: str | None = None
    shipper_name: str | None = None
    pickup_location: str | None = None
    pickup_date: str | None = None
    delivery_location: str | None = None
    delivery_date: str | None = None
    rate: float | None = None
    rate_per_mile: float | None = None
    miles: float | None = None
    equipment_type: str | None = None
    weight: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    reference_numbers: list[str] = Field(default_factory=list)
    accessorials: list[str] = Field(default_factory=list)
    detention_terms: str | None = None


class InvoiceData(BaseModel):
    """Extracted data from an invoice."""
    invoice_number: str | None = None
    load_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    broker_name: str | None = None
    broker_mc: str | None = None
    total_amount: float | None = None
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    payment_terms: str | None = None


class BillOfLadingData(BaseModel):
    """Extracted data from a bill of lading."""
    bol_number: str | None = None
    load_number: str | None = None
    pro_number: str | None = None
    shipper_name: str | None = None
    consignee_name: str | None = None
    pickup_location: str | None = None
    delivery_location: str | None = None
    driver_name: str | None = None
    equipment_type: str | None = None
    weight: str | None = None
    reference_number: str | None = None


class ProofOfDeliveryData(BaseModel):
    """Extracted data from a proof of delivery document."""
    load_number: str | None = None
    pro_number: str | None = None
    bol_number: str | None = None
    rate_conf_number: str | None = None
    ship_date: str | None = None
    delivery_date: str | None = None
    delivered_to: str | None = None
    signed_for_by: str | None = None
    condition: str | None = None
    detention_mentioned: bool = False


class LumperReceiptData(BaseModel):
    """Extracted data from a lumper receipt."""
    receipt_number: str | None = None
    load_number: str | None = None
    pro_number: str | None = None
    bol_number: str | None = None
    facility_name: str | None = None
    service_time: str | None = None
    total_fee: float | None = None


class QueryRequest(BaseModel):
    """Request model for RAG queries."""
    query: str = Field(..., min_length=1, description="The user's question")
    document_types: list[DocumentType] | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    include_sources: bool = True


class QueryResponse(BaseModel):
    """Response model for RAG queries."""
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    processing_time_ms: float


class ExtractionRequest(BaseModel):
    """Request to extract structured data from a document."""
    document_id: str
    extraction_type: str  # "rate_confirmation", "invoice", etc.


class ExtractionResponse(BaseModel):
    """Response with extracted structured data."""
    document_id: str
    extraction_type: str
    data: dict[str, Any]
    confidence: float
    raw_text_preview: str
