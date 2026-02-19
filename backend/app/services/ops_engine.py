"""Core engine for autonomous SHAMS dispatch, ticketing, billing, and copilot workflows."""
from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import logger
from app.models.document import Document, DocumentStatus, DocumentType, QueryRequest
from app.models.ops import (
    AutonomyRunRequest,
    AutonomyRunResponse,
    BillingReadinessRecord,
    ConfidenceField,
    CopilotQueryRequest,
    CopilotQueryResponse,
    DemoPackSeedRequest,
    LoadAssignmentRequest,
    LoadCreateRequest,
    LoadRecord,
    LoadStatusTransitionRequest,
    LoadStatus,
    LoadUpdateRequest,
    OpsMetricsSnapshot,
    RuleResult,
    RuleSeverity,
    SamsaraSyncRequest,
    TicketDecisionRequest,
    TicketReviewRequest,
    TicketReviewResult,
    TicketStatus,
)
from app.services.document_registry import document_registry
from app.services.document_processor import document_processor
from app.services.embeddings import embedding_service
from app.services.free_roam_agent import FreeRoamAgent
from app.services.ops_state import ops_state_store
from app.services.rag_engine import rag_engine
from app.services.vector_store import vector_store


class OpsEngine:
    """Business orchestration layer for the SHAMS autonomous MVP."""

    TICKET_PATTERN = re.compile(r"\b(?:ticket|tkt|tk)\b\s*#?\s*[:\-]?\s*([A-Z0-9\-]{5,})\b", re.IGNORECASE)
    LOAD_ID_PATTERN = re.compile(r"\bLOAD[-_ ]?(\d{3,}[A-Z0-9]*)\b", re.IGNORECASE)
    GREETING_PATTERN = re.compile(r"^\s*(hi|hello|hey|yo|sup|good (morning|afternoon|evening))[\s!.?]*$", re.IGNORECASE)
    ALLOWED_STATUS_TRANSITIONS = {
        LoadStatus.PLANNED.value: {LoadStatus.ASSIGNED.value, LoadStatus.BLOCKED.value},
        LoadStatus.ASSIGNED.value: {LoadStatus.EN_ROUTE.value, LoadStatus.BLOCKED.value, LoadStatus.DELIVERED.value},
        LoadStatus.EN_ROUTE.value: {LoadStatus.DELIVERED.value, LoadStatus.BLOCKED.value},
        LoadStatus.BLOCKED.value: {
            LoadStatus.PLANNED.value,
            LoadStatus.ASSIGNED.value,
            LoadStatus.EN_ROUTE.value,
            LoadStatus.DELIVERED.value,
        },
        LoadStatus.DELIVERED.value: set(),
    }

    def __init__(self) -> None:
        self.settings = get_settings()
        self._free_roam_agent = FreeRoamAgent(self)

    def free_roam_ready(self) -> bool:
        return self._free_roam_agent.is_enabled()

    @classmethod
    def _normalize_status(cls, status: Any) -> str:
        if isinstance(status, LoadStatus):
            return status.value
        return str(status or "").strip().lower()

    @classmethod
    def _validate_status_transition(cls, current_status: str, next_status: str) -> None:
        if current_status == next_status:
            return
        allowed = cls.ALLOWED_STATUS_TRANSITIONS.get(current_status)
        if allowed is None:
            raise ValueError(f"Unknown current status '{current_status}'")
        if next_status not in allowed:
            raise ValueError(
                f"Invalid status transition {current_status} -> {next_status}. "
                f"Allowed: {sorted(allowed)}"
            )

    def create_load(self, request: LoadCreateRequest, tenant_id: str, actor: str) -> Dict[str, Any]:
        load_id = request.load_id or ops_state_store.generate_load_id(tenant_id)
        record = LoadRecord(
            load_id=load_id,
            customer=request.customer,
            broker=request.broker,
            pickup_location=request.pickup_location,
            delivery_location=request.delivery_location,
            pickup_time=request.pickup_time,
            delivery_time=request.delivery_time,
            equipment_type=request.equipment_type,
            planned_miles=request.planned_miles,
            rate_total=request.rate_total,
            zone=request.zone,
            priority=request.priority,
            notes=request.notes,
            source=request.source,
            status=LoadStatus.PLANNED,
            version=1,
        )
        row = ops_state_store.upsert_load(tenant_id, record)
        ops_state_store.record_timeline_event(
            tenant_id,
            load_id,
            event_type="load_created",
            actor=actor,
            details={"source": request.source, "priority": request.priority},
        )
        return row

    def update_load(self, load_id: str, request: LoadUpdateRequest, tenant_id: str, actor: str) -> Dict[str, Any]:
        existing = ops_state_store.get_load(tenant_id, load_id)
        if not existing:
            raise KeyError(load_id)

        patch = request.model_dump(exclude_none=True)
        expected_version = patch.pop("expected_version", None)
        current_version = int(existing.get("version") or 1)
        if expected_version is not None and int(expected_version) != current_version:
            raise ValueError(
                f"Version conflict for {load_id}. expected={expected_version} current={current_version}"
            )
        if "status" in patch:
            requested_status = self._normalize_status(patch["status"])
            self._validate_status_transition(
                self._normalize_status(existing.get("status", LoadStatus.PLANNED.value)),
                requested_status,
            )
            patch["status"] = requested_status
        existing.update(patch)
        existing["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        existing["version"] = current_version + 1
        row = ops_state_store.upsert_load(tenant_id, LoadRecord(**existing))
        ops_state_store.record_timeline_event(
            tenant_id,
            load_id,
            event_type="load_updated",
            actor=actor,
            details={"fields": sorted(patch.keys())},
        )
        return row

    def assign_load(self, request: LoadAssignmentRequest, tenant_id: str, actor: str) -> Dict[str, Any]:
        existing = ops_state_store.get_load(tenant_id, request.load_id)
        if not existing:
            raise KeyError(request.load_id)
        self._validate_status_transition(
            self._normalize_status(existing.get("status", LoadStatus.PLANNED.value)),
            LoadStatus.ASSIGNED.value,
        )

        if request.auto:
            assignment = ops_state_store.auto_assign_load(tenant_id, request.load_id)
            mode = "autonomous"
        else:
            if not request.driver_id:
                raise ValueError("driver_id is required when auto=false")
            assignment = ops_state_store.assign_load(
                tenant_id,
                request.load_id,
                request.driver_id,
                request.truck_id,
                request.trailer_id,
                mode="manual",
            )
            mode = "manual"

        ops_state_store.record_timeline_event(
            tenant_id,
            request.load_id,
            event_type="load_assigned",
            actor=actor,
            details={"mode": mode, **assignment},
        )
        return assignment

    def transition_load_status(
        self,
        load_id: str,
        request: LoadStatusTransitionRequest,
        tenant_id: str,
        actor: str,
    ) -> Dict[str, Any]:
        existing = ops_state_store.get_load(tenant_id, load_id)
        if not existing:
            raise KeyError(load_id)

        current_version = int(existing.get("version") or 1)
        if request.expected_version is not None and int(request.expected_version) != current_version:
            raise ValueError(
                f"Version conflict for {load_id}. expected={request.expected_version} current={current_version}"
            )

        current_status = self._normalize_status(existing.get("status", LoadStatus.PLANNED.value))
        next_status = self._normalize_status(request.status)
        self._validate_status_transition(current_status, next_status)

        existing["status"] = next_status
        existing["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        existing["version"] = current_version + 1
        row = ops_state_store.upsert_load(tenant_id, LoadRecord(**existing))
        ops_state_store.record_timeline_event(
            tenant_id,
            load_id,
            event_type="load_status_transition",
            actor=actor,
            details={
                "from_status": current_status,
                "to_status": next_status,
                "version": row.get("version"),
            },
        )
        return row

    def dispatch_board(self, tenant_id: str, status: Optional[LoadStatus] = None) -> Dict[str, Any]:
        loads = ops_state_store.list_loads(tenant_id, status=status)
        metrics = ops_state_store.metrics_snapshot(tenant_id)
        return {
            "counts_by_status": metrics.get("counts_by_status", {}),
            "loads": loads,
            "drivers": ops_state_store.list_drivers(tenant_id),
        }

    def dispatch_send(self, tenant_id: str, load_id: str, actor: str) -> Dict[str, Any]:
        normalized = str(load_id or "").strip().upper()
        if not normalized:
            raise ValueError("load_id is required")

        load = ops_state_store.get_load(tenant_id, normalized)
        if not load:
            raise KeyError(normalized)

        status = self._normalize_status(load.get("status", LoadStatus.PLANNED.value))
        if status == LoadStatus.PLANNED.value:
            self.assign_load(LoadAssignmentRequest(load_id=normalized, auto=True), tenant_id=tenant_id, actor=actor)
            load = ops_state_store.get_load(tenant_id, normalized) or load

        assignment = load.get("assignment") or {}
        driver_id = str(assignment.get("driver_id") or "")
        if not driver_id:
            raise ValueError(f"Load {normalized} has no driver assignment.")

        payload = {
            "load_id": normalized,
            "driver_id": driver_id,
            "driver_name": assignment.get("driver_name"),
            "pickup_location": load.get("pickup_location"),
            "delivery_location": load.get("delivery_location"),
            "pickup_time": load.get("pickup_time"),
            "delivery_time": load.get("delivery_time"),
            "status": "sent",
            "channel": "driver_app",
        }
        receipt = ops_state_store.add_dispatch_message(tenant_id, payload)
        ops_state_store.record_timeline_event(
            tenant_id,
            normalized,
            event_type="driver_dispatch_sent",
            actor=actor,
            details={"dispatch_id": receipt.get("dispatch_id"), "driver_id": driver_id},
        )
        return receipt

    def dispatch_send_batch(self, tenant_id: str, actor: str, limit: int = 10) -> Dict[str, Any]:
        max_items = max(1, min(int(limit or 10), 50))
        board = self.dispatch_board(tenant_id)
        loads = board.get("loads", [])
        candidates = [
            row for row in loads if self._normalize_status(row.get("status")) in {"planned", "assigned", "en_route"}
        ][:max_items]

        sent: List[Dict[str, Any]] = []
        errors: List[str] = []
        for row in candidates:
            load_id = str(row.get("load_id") or "")
            if not load_id:
                continue
            try:
                sent.append(self.dispatch_send(tenant_id=tenant_id, load_id=load_id, actor=actor))
            except Exception as exc:
                errors.append(f"{load_id}: {exc}")
        return {"sent": len(sent), "receipts": sent, "errors": errors}

    def dispatch_feed(self, tenant_id: str, load_id: Optional[str] = None, limit: int = 30) -> Dict[str, Any]:
        items = ops_state_store.list_dispatch_messages(tenant_id, load_id=load_id, limit=max(1, min(int(limit or 30), 200)))
        return {"items": items, "count": len(items)}

    def _find_ticket_number(self, docs: List[Dict[str, Any]], provided_value: Optional[str]) -> Optional[str]:
        if provided_value:
            return provided_value
        for doc in docs:
            extracted = doc.get("extracted_data", {}) or {}
            for key in ("ticket_number", "receipt_number", "pro_number", "bol_number"):
                value = extracted.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip().upper()
            raw = doc.get("raw_text", "")
            match = self.TICKET_PATTERN.search(raw)
            if match:
                return match.group(1).upper()
        return None

    @staticmethod
    def _first_value(*values: Any) -> Any:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value is not None and not isinstance(value, str):
                return value
        return None

    def _collect_doc_facts(self, load_id: str, docs: List[Dict[str, Any]], request: TicketReviewRequest) -> Dict[str, Any]:
        facts: Dict[str, Any] = {
            "load_id": load_id,
            "ticket_number": self._find_ticket_number(docs, request.ticket_number),
            "customer": None,
            "broker": None,
            "rated_miles": request.rated_miles,
            "gps_miles": request.gps_miles,
            "zone": request.zone,
            "rate_total": request.expected_rate,
            "signed_for_by": None,
            "delivery_date": None,
            "pickup_date": None,
            "document_types": sorted({doc.get("document_type", "other") for doc in docs}),
            "document_ids": [doc.get("id", "") for doc in docs],
        }

        for doc in docs:
            extracted = doc.get("extracted_data", {}) or {}
            doc_type = doc.get("document_type", "other")
            if not facts["customer"]:
                facts["customer"] = self._first_value(
                    extracted.get("shipper_name"),
                    extracted.get("broker_name"),
                    extracted.get("delivered_to"),
                )
            if not facts["broker"]:
                facts["broker"] = self._first_value(extracted.get("broker_name"), extracted.get("broker_mc"))
            facts["rated_miles"] = self._first_value(facts["rated_miles"], extracted.get("miles"))
            facts["rate_total"] = self._first_value(facts["rate_total"], extracted.get("rate"), extracted.get("total_amount"))
            facts["zone"] = self._first_value(facts["zone"], extracted.get("zone"), extracted.get("delivery_region"))
            facts["signed_for_by"] = self._first_value(facts["signed_for_by"], extracted.get("signed_for_by"))
            facts["delivery_date"] = self._first_value(facts["delivery_date"], extracted.get("delivery_date"))
            facts["pickup_date"] = self._first_value(facts["pickup_date"], extracted.get("pickup_date"))

            if doc_type == DocumentType.LUMPER_RECEIPT.value and not facts["ticket_number"]:
                facts["ticket_number"] = self._first_value(extracted.get("receipt_number"))

        return facts

    @staticmethod
    def _normalize_token(value: Any) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

    @staticmethod
    def _normalize_query_for_intents(value: str) -> str:
        text = str(value or "").lower()
        replacements = {
            "laods": "loads",
            "invocie": "invoice",
            "ticet": "ticket",
            "reivew": "review",
            "whcih": "which",
            "sota": "soto",
            "drop off": "dropoff",
            "pick up": "pickup",
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)
        return text

    def _extract_ticket_reference(self, query: str) -> Optional[str]:
        raw = query or ""
        match = self.TICKET_PATTERN.search(raw)
        if match:
            normalized = self._normalize_token(match.group(1))
            if normalized and any(ch.isdigit() for ch in normalized):
                return normalized
        compact_match = re.search(r"\bTKT[-_ ]?(\d{5,})\b", raw, re.IGNORECASE)
        if compact_match:
            return self._normalize_token(compact_match.group(1))
        return None

    @staticmethod
    def _safe_rows(rows: Any) -> List[Dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _normalize_load_id(self, value: Any) -> Optional[str]:
        cleaned = str(value or "").strip().upper().replace("-", "").replace("_", "")
        if not cleaned:
            return None
        suffix = cleaned[4:] if cleaned.startswith("LOAD") else cleaned
        if not suffix:
            return None

        numeric_match = re.fullmatch(r"0*(\d+)([A-Z0-9]*)", suffix)
        if numeric_match:
            digits = str(int(numeric_match.group(1)))
            tail = numeric_match.group(2)
            return f"LOAD{digits.zfill(5)}{tail}"
        return f"LOAD{suffix}"

    def _resolve_load_id_from_lookup(self, candidate: Optional[str], load_lookup: Dict[str, Dict[str, Any]]) -> Optional[str]:
        if not candidate:
            return None
        normalized = self._normalize_token(candidate)
        if candidate in load_lookup:
            return candidate
        for load_id in load_lookup.keys():
            if self._normalize_token(load_id) == normalized:
                return load_id
        return None

    def _match_driver_from_query(self, query: str, drivers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not drivers:
            return None

        query_norm = re.sub(r"[^a-z0-9 ]", " ", str(query or "").lower())
        query_tokens = [token for token in query_norm.split() if len(token) > 1]
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0

        for driver in drivers:
            name = str(driver.get("name") or "").strip()
            if not name:
                continue
            name_norm = re.sub(r"[^a-z0-9 ]", " ", name.lower())
            name_tokens = [token for token in name_norm.split() if len(token) > 1]
            if not name_tokens:
                continue

            score = 0.0
            if name_norm in query_norm:
                score = 1.0
            else:
                if name_tokens[0] in query_tokens:
                    score = max(score, 0.88)
                for n_token in name_tokens:
                    if n_token in query_tokens:
                        score = max(score, 0.82)
                        continue
                    for q_token in query_tokens:
                        ratio = SequenceMatcher(None, q_token, n_token).ratio()
                        if ratio >= 0.9:
                            score = max(score, 0.8)
                        elif ratio >= 0.84:
                            score = max(score, 0.74)
            if score > best_score:
                best = driver
                best_score = score

        return best if best_score >= 0.74 else None

    def _loads_for_driver(self, loads: List[Dict[str, Any]], driver: Dict[str, Any]) -> List[Dict[str, Any]]:
        driver_id = str(driver.get("driver_id") or "").strip().upper()
        driver_name = self._normalize_token(driver.get("name"))
        matched: List[Dict[str, Any]] = []
        for load in loads:
            assignment = load.get("assignment") or {}
            assignment_driver_id = str(assignment.get("driver_id") or "").strip().upper()
            assignment_driver_name = self._normalize_token(assignment.get("driver_name"))
            if driver_id and assignment_driver_id == driver_id:
                matched.append(load)
                continue
            if driver_name and assignment_driver_name and assignment_driver_name == driver_name:
                matched.append(load)
        return matched

    def _confidence_profile(self, facts: Dict[str, Any], docs: List[Dict[str, Any]], load: Dict[str, Any]) -> List[ConfidenceField]:
        doc_count = max(1, len(docs))
        strong_source = min(0.999, 0.93 + (0.015 * min(doc_count, 4)))

        profile = [
            ConfidenceField(field="load_id", value=facts.get("load_id"), confidence=0.999, source="system"),
            ConfidenceField(
                field="ticket_number",
                value=facts.get("ticket_number"),
                confidence=strong_source if facts.get("ticket_number") else 0.0,
                source="documents",
            ),
            ConfidenceField(
                field="customer",
                value=facts.get("customer") or load.get("customer"),
                confidence=strong_source if (facts.get("customer") or load.get("customer")) else 0.0,
                source="documents",
            ),
            ConfidenceField(
                field="rated_miles",
                value=facts.get("rated_miles") or load.get("planned_miles"),
                confidence=0.99 if (facts.get("rated_miles") or load.get("planned_miles")) else 0.0,
                source="rate_confirmation",
            ),
            ConfidenceField(
                field="gps_miles",
                value=facts.get("gps_miles"),
                confidence=0.99 if facts.get("gps_miles") is not None else 0.0,
                source="samsara",
            ),
            ConfidenceField(
                field="rate_total",
                value=facts.get("rate_total") or load.get("rate_total"),
                confidence=0.992 if (facts.get("rate_total") or load.get("rate_total")) else 0.0,
                source="invoice_or_ratecon",
            ),
            ConfidenceField(
                field="zone",
                value=facts.get("zone") or load.get("zone"),
                confidence=0.99 if (facts.get("zone") or load.get("zone")) else 0.0,
                source="routing",
            ),
            ConfidenceField(
                field="signature",
                value=facts.get("signed_for_by"),
                confidence=0.99 if facts.get("signed_for_by") else 0.0,
                source="pod",
            ),
        ]
        return profile

    def _rule_results(self, facts: Dict[str, Any], load: Dict[str, Any], docs: List[Dict[str, Any]]) -> tuple[List[RuleResult], List[str], List[str]]:
        rules: List[RuleResult] = []
        leakage: List[str] = []

        required_docs = {
            DocumentType.RATE_CONFIRMATION.value,
            DocumentType.BOL.value,
            DocumentType.POD.value,
        }
        present_docs = {doc.get("document_type", "other") for doc in docs}
        missing_docs = sorted(required_docs - present_docs)
        rules.append(
            RuleResult(
                rule_id="docs.required",
                description="Required billing documents are present",
                severity=RuleSeverity.BLOCK,
                passed=not missing_docs,
                evidence={"missing_documents": missing_docs},
                suggestion="Upload missing source docs before billing" if missing_docs else None,
            )
        )

        required_fields = ["ticket_number", "customer", "rated_miles", "rate_total"]
        missing_fields = [field for field in required_fields if not facts.get(field)]
        rules.append(
            RuleResult(
                rule_id="fields.required",
                description="Critical fields are populated",
                severity=RuleSeverity.BLOCK,
                passed=not missing_fields,
                evidence={"missing_fields": missing_fields},
                suggestion="Backfill missing fields from ticket image or source docs" if missing_fields else None,
            )
        )

        rated = facts.get("rated_miles") or load.get("planned_miles")
        gps = facts.get("gps_miles")
        miles_variance_threshold = float(self.settings.ticket_miles_variance_threshold)
        if rated and gps:
            variance = abs(float(gps) - float(rated)) / max(1.0, float(rated))
            passed = variance <= miles_variance_threshold
            rules.append(
                RuleResult(
                    rule_id="miles.variance",
                    description=f"GPS miles within {round(miles_variance_threshold * 100, 1)}% of rated miles",
                    severity=RuleSeverity.BLOCK,
                    passed=passed,
                    evidence={"gps_miles": gps, "rated_miles": rated, "variance": round(variance, 4)},
                    suggestion="Adjust zone/rate due to mile variance" if not passed else None,
                )
            )
            if not passed:
                leakage.append(
                    f"Mileage mismatch exceeds {round(miles_variance_threshold * 100, 1)}% and may under/over bill the load"
                )
        else:
            rules.append(
                RuleResult(
                    rule_id="miles.variance",
                    description=f"GPS miles within {round(miles_variance_threshold * 100, 1)}% of rated miles",
                    severity=RuleSeverity.WARN,
                    passed=False,
                    evidence={"gps_miles": gps, "rated_miles": rated},
                    suggestion="Sync Samsara telemetry before final billing",
                )
            )

        zone_expected = load.get("zone")
        zone_found = facts.get("zone")
        if zone_expected and zone_found:
            zone_ok = str(zone_expected).strip().upper() == str(zone_found).strip().upper()
            rules.append(
                RuleResult(
                    rule_id="zone.match",
                    description="Ticket zone matches load zone",
                    severity=RuleSeverity.BLOCK,
                    passed=zone_ok,
                    evidence={"expected": zone_expected, "found": zone_found},
                    suggestion="Update zone before export to avoid rate leakage" if not zone_ok else None,
                )
            )
            if not zone_ok:
                leakage.append("Zone mismatch detected")

        signature_ok = bool(facts.get("signed_for_by"))
        rules.append(
            RuleResult(
                rule_id="pod.signature",
                description="Proof of delivery contains signature",
                severity=RuleSeverity.WARN,
                passed=signature_ok,
                evidence={"signed_for_by": facts.get("signed_for_by")},
                suggestion="Request signed POD before billing if customer requires it" if not signature_ok else None,
            )
        )

        expected_rate = load.get("rate_total")
        found_rate = facts.get("rate_total")
        if expected_rate and found_rate:
            diff = abs(float(found_rate) - float(expected_rate))
            pct = diff / max(1.0, float(expected_rate))
            ok = pct <= 0.02
            rules.append(
                RuleResult(
                    rule_id="rate.match",
                    description="Invoice/rate confirmation aligns with planned rate",
                    severity=RuleSeverity.WARN,
                    passed=ok,
                    evidence={"expected_rate": expected_rate, "found_rate": found_rate, "difference": round(diff, 2)},
                    suggestion="Review accessorials or negotiated rate change" if not ok else None,
                )
            )
            if not ok:
                leakage.append("Rate mismatch against planned rate")

        bol_count = sum(1 for doc in docs if doc.get("document_type") == DocumentType.BOL.value)
        rules.append(
            RuleResult(
                rule_id="split_ticket.check",
                description="Split-ticket pattern is reconciled",
                severity=RuleSeverity.WARN,
                passed=bol_count <= 1 or bool(facts.get("ticket_number")),
                evidence={"bol_count": bol_count, "ticket_number": facts.get("ticket_number")},
                suggestion="Verify split-load ticket references" if bol_count > 1 else None,
            )
        )

        return rules, leakage, missing_docs

    @staticmethod
    def _final_confidence(confidence_profile: List[ConfidenceField], rules: Optional[List[RuleResult]] = None) -> float:
        if not confidence_profile:
            return 0.0
        values = [float(row.confidence or 0.0) for row in confidence_profile]
        if not values:
            return 0.0
        base = sum(values) / len(values)
        if not rules:
            return round(base, 4)
        passed = sum(1 for row in rules if row.passed)
        rule_ratio = passed / max(1, len(rules))
        return round(base * rule_ratio, 4)

    @staticmethod
    def _failed_rule_descriptions(rules: List[RuleResult]) -> List[str]:
        descriptions: list[str] = []
        for rule in rules:
            if rule.passed:
                continue
            hint = f": {rule.suggestion}" if rule.suggestion else ""
            descriptions.append(f"{rule.rule_id} ({rule.severity.value}){hint}")
        return descriptions

    def _build_exception_reason(
        self,
        base_reason: str,
        rules: List[RuleResult],
        missing_docs: List[str],
    ) -> str:
        chunks = [base_reason]
        failed = self._failed_rule_descriptions(rules)
        if failed:
            chunks.append(f"Failed checks: {'; '.join(failed[:3])}")
        if missing_docs:
            chunks.append(f"Missing docs: {', '.join(missing_docs)}")
        return " | ".join(chunk for chunk in chunks if chunk)

    def _is_auto_approved(self, confidence_profile: List[ConfidenceField], rules: List[RuleResult]) -> tuple[bool, str]:
        critical_fields = {"ticket_number", "customer", "rated_miles", "gps_miles", "rate_total", "zone"}
        profile_map = {row.field: row for row in confidence_profile}
        confidence_threshold = float(self.settings.ticket_confidence_threshold)

        missing_critical = [field for field in critical_fields if field not in profile_map]
        if missing_critical:
            return False, "Missing critical confidence fields"

        low_conf = [
            field
            for field in critical_fields
            if profile_map[field].confidence < confidence_threshold or profile_map[field].value in (None, "")
        ]
        if low_conf:
            return False, (
                f"Confidence below threshold ({round(confidence_threshold, 3)}): "
                f"{', '.join(sorted(low_conf))}"
            )

        blocking_failures = [rule for rule in rules if rule.severity == RuleSeverity.BLOCK and not rule.passed]
        if blocking_failures:
            return False, f"Blocking validation failure: {blocking_failures[0].rule_id}"

        return True, "Auto-approved: confidence and validation thresholds passed"

    def _review_ticket_core(self, request: TicketReviewRequest, tenant_id: str, actor: str) -> TicketReviewResult:
        started = time.perf_counter()
        load = ops_state_store.get_load(tenant_id, request.load_id)
        if not load:
            raise KeyError(f"Load not found: {request.load_id}")

        docs = []
        if request.document_ids:
            for doc_id in request.document_ids:
                row = document_registry.get(doc_id, tenant_id=tenant_id)
                if row:
                    docs.append(row)
        else:
            docs = document_registry.find_related(request.load_id, tenant_id=tenant_id)

        # Prefer explicit GPS miles from request; otherwise use the latest synced
        # telemetry event for this load within the configured lookback window.
        if request.gps_miles is None:
            telemetry_miles = ops_state_store.latest_samsara_miles(
                tenant_id=tenant_id,
                load_id=request.load_id,
                hours_back=request.gps_hours_back,
            )
            if telemetry_miles is not None:
                request = request.model_copy(update={"gps_miles": telemetry_miles})

        facts = self._collect_doc_facts(request.load_id, docs, request)
        confidence_profile = self._confidence_profile(facts, docs, load)
        rules, leakage_findings, missing_docs = self._rule_results(facts, load, docs)
        auto_approved, reason = self._is_auto_approved(confidence_profile, rules)
        final_confidence = self._final_confidence(confidence_profile, rules)
        failed_rules = self._failed_rule_descriptions(rules)
        if not auto_approved:
            reason = self._build_exception_reason(reason, rules, missing_docs)

        status = TicketStatus.APPROVED if auto_approved else TicketStatus.EXCEPTION
        review_id = f"REV-{ops_state_store.next_sequence(tenant_id, 'review'):06d}"
        processing_time_ms = round((time.perf_counter() - started) * 1000, 2)

        result = TicketReviewResult(
            review_id=review_id,
            load_id=request.load_id,
            ticket_number=facts.get("ticket_number"),
            status=status,
            auto_approved=auto_approved,
            approval_reason=reason,
            final_confidence=final_confidence,
            confidence_profile=confidence_profile,
            rules=rules,
            failed_rules=failed_rules,
            leakage_findings=leakage_findings,
            billing_ready=auto_approved and not missing_docs,
            processing_time_ms=processing_time_ms,
            documents_used=[doc.get("filename", "") for doc in docs],
            missing_documents=missing_docs,
        )

        payload = result.model_dump(mode="json")
        ops_state_store.store_review(tenant_id, payload)
        ops_state_store.record_timeline_event(
            tenant_id,
            request.load_id,
            event_type="ticket_reviewed",
            actor=actor,
            details={
                "review_id": review_id,
                "auto_approved": auto_approved,
                "status": status.value,
                "latency_ms": processing_time_ms,
            },
        )

        if auto_approved:
            self._mark_load_complete_for_ticket(
                tenant_id=tenant_id,
                load_id=request.load_id,
                actor=actor,
                reason="ticket_auto_approved",
            )

        return result

    async def review_ticket(self, request: TicketReviewRequest, tenant_id: str, actor: str) -> TicketReviewResult:
        return self._review_ticket_core(request=request, tenant_id=tenant_id, actor=actor)

    def ticket_queue(self, tenant_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return ops_state_store.list_reviews(tenant_id, status=status)

    def ticket_dossier(self, tenant_id: str, load_id: str) -> Dict[str, Any]:
        load = ops_state_store.get_load(tenant_id, load_id)
        if not load:
            raise KeyError(load_id)

        reviews = [row for row in ops_state_store.list_reviews(tenant_id) if str(row.get("load_id")) == load_id]
        latest_review = reviews[0] if reviews else None
        billing_rows = [row for row in ops_state_store.list_billing(tenant_id) if str(row.get("load_id")) == load_id]
        billing = billing_rows[0] if billing_rows else None
        docs = document_registry.find_related(load_id, tenant_id=tenant_id)

        return {
            "load": load,
            "latest_review": latest_review,
            "reviews": reviews[:20],
            "billing": billing,
            "documents": [
                {
                    "id": doc.get("id"),
                    "filename": doc.get("filename"),
                    "document_type": doc.get("document_type"),
                    "updated_at": doc.get("updated_at"),
                }
                for doc in docs[:20]
            ],
        }

    def apply_ticket_decision(
        self,
        review_id: str,
        request: TicketDecisionRequest,
        tenant_id: str,
        actor: str,
    ) -> Dict[str, Any]:
        decision = request.decision.strip().lower()
        status_map = {
            "approve": TicketStatus.APPROVED.value,
            "resolve": TicketStatus.RESOLVED.value,
            "reject": TicketStatus.EXCEPTION.value,
        }
        if decision not in status_map:
            raise ValueError("decision must be one of: approve, resolve, reject")
        row = ops_state_store.set_review_status(tenant_id, review_id, status_map[decision], note=request.note or "")
        ops_state_store.record_timeline_event(
            tenant_id,
            row["load_id"],
            event_type="ticket_decision",
            actor=actor,
            details={"review_id": review_id, "decision": decision, "note": request.note},
        )
        if status_map[decision] in {TicketStatus.APPROVED.value, TicketStatus.RESOLVED.value}:
            self._mark_load_complete_for_ticket(
                tenant_id=tenant_id,
                load_id=row["load_id"],
                actor=actor,
                reason=f"ticket_{decision}",
            )
        return row

    def billing_readiness(self, tenant_id: str) -> List[BillingReadinessRecord]:
        return [BillingReadinessRecord(**row) for row in ops_state_store.list_billing(tenant_id)]

    def create_mcleod_export(self, load_id: str, tenant_id: str, actor: str) -> Dict[str, Any]:
        load = ops_state_store.get_load(tenant_id, load_id)
        if not load:
            raise KeyError(load_id)

        related_reviews = [row for row in ops_state_store.list_reviews(tenant_id) if row.get("load_id") == load_id]
        latest_review = related_reviews[0] if related_reviews else None
        billing_rows = [row for row in ops_state_store.list_billing(tenant_id) if row.get("load_id") == load_id]
        billing = billing_rows[0] if billing_rows else {}
        docs = document_registry.find_related(load_id, tenant_id=tenant_id)

        payload = {
            "schema_version": "mvp-v1",
            "generated_by": actor,
            "load": load,
            "billing": billing,
            "latest_review": latest_review,
            "documents": [
                {
                    "id": doc.get("id"),
                    "filename": doc.get("filename"),
                    "type": doc.get("document_type"),
                    "load_ids": doc.get("load_ids", []),
                }
                for doc in docs
            ],
        }

        export = ops_state_store.add_export(tenant_id, load_id, payload)
        ops_state_store.record_timeline_event(
            tenant_id,
            load_id,
            event_type="mcleod_export_generated",
            actor=actor,
            details={"export_id": export["export_id"], "artifact_path": export["artifact_path"]},
        )
        return export

    def list_mcleod_exports(self, tenant_id: str) -> List[Dict[str, Any]]:
        return ops_state_store.list_exports(tenant_id)

    def replay_mcleod_export(self, export_id: str, tenant_id: str, actor: str) -> Dict[str, Any]:
        row = ops_state_store.replay_export(tenant_id, export_id)
        ops_state_store.record_timeline_event(
            tenant_id,
            row["load_id"],
            event_type="mcleod_export_replayed",
            actor=actor,
            details={"export_id": export_id},
        )
        return row

    def _fetch_samsara_events(self, tenant_id: str, load_ids: List[str], hours_back: int) -> List[Dict[str, Any]]:
        token = (self.settings.samsara_api_token or "").strip()
        events_url = (self.settings.samsara_events_url or "").strip()

        if not token:
            raise RuntimeError("Samsara sync requires SAMSARA_API_TOKEN.")
        if not events_url:
            raise RuntimeError("Samsara sync requires SAMSARA_EVENTS_URL.")

        payload = {
            "tenant_id": tenant_id,
            "hours_back": hours_back,
            "load_ids": load_ids,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(timeout=12.0) as client:
                response = client.post(events_url, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            raise RuntimeError(f"Samsara sync request failed: {exc}") from exc

        events = body.get("events")
        if not isinstance(events, list):
            raise RuntimeError("Invalid Samsara sync response: expected JSON field 'events' as an array.")
        return events

    def sync_samsara(self, request: SamsaraSyncRequest, tenant_id: str, actor: str) -> Dict[str, Any]:
        synced: List[Dict[str, Any]] = []
        unmatched = 0
        load_ids = request.load_ids or [row.get("load_id") for row in ops_state_store.list_loads(tenant_id)[:30]]
        if not load_ids:
            return {
                "synced": 0,
                "hours_back": request.hours_back,
                "events": [],
                "source": "samsara_live",
                "unmatched": 0,
            }

        events = self._fetch_samsara_events(tenant_id, load_ids, request.hours_back)
        for event in events:
            if not isinstance(event, dict):
                continue

            load_id = str(event.get("load_id", "")).strip().upper()
            if not load_id:
                continue
            load = ops_state_store.get_load(tenant_id, load_id)
            if not load:
                unmatched += 1
                continue

            gps_raw = event.get("gps_miles")
            try:
                gps_miles = round(float(gps_raw), 2)
            except Exception:
                continue

            planned = float(load.get("planned_miles") or 0.0)
            trip = {
                "load_id": load_id,
                "gps_miles": gps_miles,
                "planned_miles": planned,
                "stop_events": int(event.get("stop_events") or 0),
                "vehicle_id": event.get("vehicle_id"),
                "window_start": event.get("window_start"),
                "window_end": event.get("window_end"),
                "source": "samsara_live",
            }
            synced.append(trip)
            ops_state_store.record_timeline_event(
                tenant_id,
                load_id,
                event_type="samsara_synced",
                actor=actor,
                details=trip,
            )

        return {
            "synced": len(synced),
            "hours_back": request.hours_back,
            "events": synced,
            "source": "samsara_live",
            "unmatched": unmatched,
        }

    def _build_system_context(self, tenant_id: str) -> str:
        board = self.dispatch_board(tenant_id)
        loads = board.get("loads", [])
        drivers = board.get("drivers", [])
        billing_stats = ops_state_store.metrics_snapshot(tenant_id)

        driver_summary = []
        if not drivers:
            driver_summary.append("No drivers found.")
        else:
            by_status: dict[str, list[str]] = {}
            for driver in drivers:
                status = str(driver.get("status", "unknown")).lower()
                by_status.setdefault(status, []).append(f"{driver.get('name')} ({driver.get('truck_id', '-')})")
            for status, names in by_status.items():
                driver_summary.append(f"- {status.title()}: {', '.join(names)}")

        load_summary = []
        active = [row for row in loads if row.get("status") != "delivered"]
        if not active:
            load_summary.append("No active loads.")
        else:
            load_summary.append(f"Active Loads ({len(active)}):")
            for row in active[:8]:
                load_summary.append(
                    f"- {row.get('load_id')}: {row.get('customer')} ({row.get('status')}) -> {row.get('delivery_location')}"
                )
            if len(active) > 8:
                load_summary.append(f"... and {len(active) - 8} more.")

        billing_summary = (
            f"Billing Ready: {int((billing_stats.get('billing_ready_rate', 0) or 0) * 100)}%, "
            f"Recovered: ${int(billing_stats.get('estimated_leakage_recovered_usd', 0) or 0)}"
        )
        return (
            "CURRENT SYSTEM STATE (Live Data):\n"
            f"Drivers:\n{chr(10).join(driver_summary)}\n\n"
            f"Dispatch:\n{chr(10).join(load_summary)}\n\n"
            f"Metrics: {billing_summary}\n"
        )

    def _extract_load_id(self, query: str, explicit: Optional[str] = None) -> Optional[str]:
        if explicit and explicit.strip():
            return self._normalize_load_id(explicit)
        match = self.LOAD_ID_PATTERN.search(query or "")
        if not match:
            return None
        return self._normalize_load_id(f"LOAD{match.group(1)}")

    def _extract_load_ids(self, query: str) -> List[str]:
        found: list[str] = []
        for match in self.LOAD_ID_PATTERN.finditer(query or ""):
            normalized = self._normalize_load_id(f"LOAD{match.group(1)}")
            if normalized and normalized not in found:
                found.append(normalized)
        return found

    def _release_drivers_from_completed_reviews(self, tenant_id: str, actor: str = "copilot") -> int:
        reviews = ops_state_store.list_reviews(tenant_id)
        cleared_loads = {
            str(row.get("load_id")): str(row.get("status", "")).lower()
            for row in reviews
            if str(row.get("status", "")).lower() in {TicketStatus.APPROVED.value, TicketStatus.RESOLVED.value}
        }
        released_drivers: set[str] = set()
        for load in ops_state_store.list_loads(tenant_id):
            load_id = str(load.get("load_id") or "")
            if not load_id:
                continue
            if str(load.get("status", "")).lower() not in {LoadStatus.ASSIGNED.value, LoadStatus.EN_ROUTE.value}:
                continue
            if load_id not in cleared_loads:
                continue
            assignment = load.get("assignment") or {}
            driver_id = assignment.get("driver_id")
            if not driver_id:
                continue
            if driver_id in released_drivers:
                continue
            ops_state_store.set_driver_status(tenant_id, driver_id, "available")
            released_drivers.add(driver_id)
            ops_state_store.record_timeline_event(
                tenant_id,
                load_id,
                event_type="driver_released",
                actor=actor,
                details={"driver_id": driver_id, "reason": "ticket_cleared"},
            )
        return len(released_drivers)

    def _mark_load_complete_for_ticket(self, tenant_id: str, load_id: str, actor: str, reason: str) -> None:
        load = ops_state_store.get_load(tenant_id, load_id)
        if not load:
            return

        current_status = self._normalize_status(load.get("status", LoadStatus.PLANNED.value))
        if current_status != LoadStatus.DELIVERED.value:
            load["status"] = LoadStatus.DELIVERED.value
            load["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            load["version"] = int(load.get("version") or 1) + 1
            ops_state_store.upsert_load(tenant_id, LoadRecord(**load))
            ops_state_store.record_timeline_event(
                tenant_id,
                load_id,
                event_type="load_status_transition",
                actor=actor,
                details={
                    "from_status": current_status,
                    "to_status": LoadStatus.DELIVERED.value,
                    "reason": reason,
                    "version": load.get("version"),
                },
            )

        assignment = load.get("assignment") or {}
        driver_id = assignment.get("driver_id")
        if driver_id:
            ops_state_store.set_driver_status(tenant_id, driver_id, "available")
            ops_state_store.record_timeline_event(
                tenant_id,
                load_id,
                event_type="driver_released",
                actor=actor,
                details={"driver_id": driver_id, "reason": reason},
            )

    def _auto_assign_planned_loads(
        self,
        tenant_id: str,
        actor: str = "copilot",
        limit: int = 20,
    ) -> tuple[int, list[str], list[Dict[str, Any]]]:
        assigned = 0
        errors: list[str] = []
        assignments: list[Dict[str, Any]] = []
        planned = [
            row for row in ops_state_store.list_loads(tenant_id)
            if str(row.get("status", "")).lower() == LoadStatus.PLANNED.value and not row.get("assignment")
        ]
        for row in planned[: max(1, limit)]:
            load_id = str(row.get("load_id") or "")
            if not load_id:
                continue
            available = [
                d for d in ops_state_store.list_drivers(tenant_id)
                if str(d.get("status", "")).lower() == "available"
            ]
            if not available:
                break
            try:
                assignment = ops_state_store.auto_assign_load(tenant_id, load_id)
                assigned += 1
                assignments.append(
                    {
                        "load_id": load_id,
                        "driver_name": assignment.get("driver_name"),
                        "driver_id": assignment.get("driver_id"),
                        "truck_id": assignment.get("truck_id"),
                    }
                )
                ops_state_store.record_timeline_event(
                    tenant_id,
                    load_id,
                    event_type="load_assigned",
                    actor=actor,
                    details={"mode": "copilot_auto_assign", **assignment},
                )
            except Exception as exc:
                errors.append(f"{load_id}: {exc}")
                break
        return assigned, errors, assignments

    def _run_quick_ticket_review(self, tenant_id: str, load_id: str, actor: str = "copilot") -> Optional[Dict[str, Any]]:
        load = ops_state_store.get_load(tenant_id, load_id)
        if not load:
            return None
        try:
            reviewed = self._review_ticket_core(
                TicketReviewRequest(
                    load_id=load_id,
                    ticket_number=f"TKT-AUTO-{int(time.time())}-{load_id[-3:]}",
                    rated_miles=float(load.get("planned_miles") or 0.0),
                    gps_miles=round(float(load.get("planned_miles") or 0.0) * 1.01, 2),
                    zone=load.get("zone"),
                    expected_rate=float(load.get("rate_total") or 0.0),
                ),
                tenant_id=tenant_id,
                actor=actor,
            )
        except Exception:
            return None
        return {
            "load_id": load_id,
            "ticket_number": reviewed.ticket_number,
            "status": reviewed.status.value,
            "final_confidence": reviewed.final_confidence,
        }

    def _finalize_approved_assigned_loads(self, tenant_id: str, actor: str = "copilot") -> list[str]:
        approved_by_load: Dict[str, Dict[str, Any]] = {}
        for review in ops_state_store.list_reviews(tenant_id):
            status = str(review.get("status", "")).lower()
            if status not in {TicketStatus.APPROVED.value, TicketStatus.RESOLVED.value}:
                continue
            load_id = str(review.get("load_id") or "")
            if load_id and load_id not in approved_by_load:
                approved_by_load[load_id] = review

        finalized: list[str] = []
        for load in ops_state_store.list_loads(tenant_id):
            load_id = str(load.get("load_id") or "")
            if not load_id or load_id not in approved_by_load:
                continue
            if str(load.get("status", "")).lower() not in {LoadStatus.ASSIGNED.value, LoadStatus.EN_ROUTE.value}:
                continue
            self._mark_load_complete_for_ticket(tenant_id=tenant_id, load_id=load_id, actor=actor, reason="ticket_preapproved")
            finalized.append(load_id)
        return finalized

    def _try_ops_state_answer(
        self,
        query: str,
        tenant_id: str,
        started: float,
    ) -> CopilotQueryResponse | None:
        q = self._normalize_query_for_intents((query or "").strip())
        if not q:
            return None

        if self.GREETING_PATTERN.match(q):
            elapsed = (time.time() - started) * 1000
            return CopilotQueryResponse(
                answer=(
                    "Hey - I can help with live dispatch actions. Ask me things like: "
                    "'which drivers are available', 'assign next load', or "
                    "'who is the broker and invoice for LOAD00030'."
                ),
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.98,
                processing_time_ms=elapsed,
            )

        board = self.dispatch_board(tenant_id)
        drivers = self._safe_rows(board.get("drivers"))
        loads = self._safe_rows(board.get("loads"))
        load_lookup = {str(row.get("load_id")): row for row in loads if row.get("load_id")}
        load_id_from_query = self._extract_load_id(query)
        resolved_load_id = self._resolve_load_id_from_lookup(load_id_from_query, load_lookup) or load_id_from_query

        if any(token in q for token in ["why", "how come"]) and "complete" in q and "load" in q:
            latest_review_by_load: Dict[str, Dict[str, Any]] = {}
            for row in ops_state_store.list_reviews(tenant_id):
                load_id = str(row.get("load_id") or "")
                if load_id and load_id not in latest_review_by_load:
                    latest_review_by_load[load_id] = row
            not_complete = []
            for row in loads:
                if str(row.get("status", "")).lower() not in {LoadStatus.ASSIGNED.value, LoadStatus.EN_ROUTE.value}:
                    continue
                latest = latest_review_by_load.get(str(row.get("load_id") or ""))
                status = str((latest or {}).get("status") or "pending_review").lower()
                if status not in {TicketStatus.APPROVED.value, TicketStatus.RESOLVED.value}:
                    not_complete.append((str(row.get("load_id") or ""), status))
            elapsed = (time.time() - started) * 1000
            if not not_complete:
                return CopilotQueryResponse(
                    answer=(
                        "Loads move to complete only after ticket status is approved/resolved. "
                        "Current assigned loads are already clear or waiting to sync."
                    ),
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.93,
                    processing_time_ms=elapsed,
                )
            preview = ", ".join(f"{load_id}({status})" for load_id, status in not_complete[:4])
            return CopilotQueryResponse(
                answer=(
                    "Those loads did not move to complete because their latest ticket status is not approved/resolved yet. "
                    f"Open items: {preview}."
                ),
                sources=[{"filename": "ticket_queue", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.95,
                processing_time_ms=elapsed,
            )

        review_ticket_intent = (
            any(token in q for token in ["review ticket", "ticket review", "run ticket review", "audit ticket", "run ticet review"])
            and not any(token in q for token in ["why", "what is wrong"])
        )
        if review_ticket_intent:
            elapsed = (time.time() - started) * 1000
            target_load_id = resolved_load_id
            if not target_load_id:
                in_progress = [
                    row for row in loads
                    if str(row.get("status", "")).lower() in {LoadStatus.ASSIGNED.value, LoadStatus.EN_ROUTE.value}
                ]
                target_load_id = str(in_progress[0].get("load_id")) if in_progress else None
            if not target_load_id:
                planned_rows = [
                    row for row in loads
                    if str(row.get("status", "")).lower() == LoadStatus.PLANNED.value
                ]
                if planned_rows:
                    target_load_id = str(planned_rows[0].get("load_id"))
                    self.assign_load(
                        LoadAssignmentRequest(load_id=target_load_id, auto=True),
                        tenant_id=tenant_id,
                        actor="copilot",
                    )
                    board = self.dispatch_board(tenant_id)
                    loads = self._safe_rows(board.get("loads"))
                    load_lookup = {str(row.get("load_id")): row for row in loads if row.get("load_id")}
            if not target_load_id:
                return CopilotQueryResponse(
                    answer="No assigned loads are waiting for ticket review right now.",
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.92,
                    processing_time_ms=elapsed,
                )
            target_load_id = self._resolve_load_id_from_lookup(target_load_id, load_lookup) or target_load_id
            if target_load_id not in load_lookup:
                return CopilotQueryResponse(
                    answer=f"{target_load_id} is not in the current dispatch board.",
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.9,
                    processing_time_ms=elapsed,
                )
            target = load_lookup[target_load_id]
            reviewed = self._review_ticket_core(
                TicketReviewRequest(
                    load_id=target_load_id,
                    ticket_number=f"TKT-AUTO-{int(time.time())}",
                    rated_miles=float(target.get("planned_miles") or 0.0),
                    gps_miles=round(float(target.get("planned_miles") or 0.0) * 1.01, 2),
                    zone=target.get("zone"),
                    expected_rate=float(target.get("rate_total") or 0.0),
                ),
                tenant_id=tenant_id,
                actor="copilot",
            )
            if reviewed.status == TicketStatus.EXCEPTION:
                return CopilotQueryResponse(
                    answer=(
                        f"Reviewed {target_load_id}: flagged {reviewed.ticket_number}. "
                        f"Reason: {reviewed.approval_reason}."
                    ),
                    sources=[{"filename": reviewed.review_id, "document_type": "ticket_review", "similarity": 1.0}],
                    confidence=max(float(reviewed.final_confidence), 0.88),
                    processing_time_ms=(time.time() - started) * 1000,
                )
            return CopilotQueryResponse(
                answer=(
                    f"Reviewed {target_load_id}: approved {reviewed.ticket_number}. "
                    "Load moved to complete and driver released."
                ),
                sources=[{"filename": reviewed.review_id, "document_type": "ticket_review", "similarity": 1.0}],
                confidence=max(float(reviewed.final_confidence), 0.9),
                processing_time_ms=(time.time() - started) * 1000,
            )

        driver_named_in_query = any(str(driver.get("name", "")).split(" ")[0].lower() in q for driver in drivers)
        driver_activity_intent = (
            any(token in q for token in ["loads did", "miles did", "how many miles", "how many loads", "which loads", "past week"])
            and driver_named_in_query
        )
        if driver_activity_intent:
            elapsed = (time.time() - started) * 1000
            driver = self._match_driver_from_query(query, drivers)
            if not driver:
                known = ", ".join(str(d.get("name") or "") for d in drivers[:6]) or "no drivers configured"
                return CopilotQueryResponse(
                    answer=f"I couldn't map that to a driver. Try one of: {known}.",
                    sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.86,
                    processing_time_ms=elapsed,
                )
            dloads = self._loads_for_driver(loads, driver)
            completed = [row for row in dloads if str(row.get("status", "")).lower() == LoadStatus.DELIVERED.value]
            active = [row for row in dloads if str(row.get("status", "")).lower() != LoadStatus.DELIVERED.value]
            completed_miles = round(sum(float(row.get("planned_miles") or 0.0) for row in completed), 1)
            total_miles = round(sum(float(row.get("planned_miles") or 0.0) for row in dloads), 1)
            head = ", ".join(str(row.get("load_id") or "") for row in completed[:5]) or "none"
            answer = (
                f"{driver.get('name')} has {len(dloads)} load(s): {len(completed)} complete, {len(active)} active. "
                f"Completed miles: {completed_miles}. Total assigned miles: {total_miles}. "
                f"Completed loads: {head}."
            )
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.94,
                processing_time_ms=elapsed,
            )

        ticket_ref = self._extract_ticket_reference(query or "")
        if ticket_ref:
            matched = None
            for review in ops_state_store.list_reviews(tenant_id):
                candidate = self._normalize_token(review.get("ticket_number"))
                if candidate and (candidate == ticket_ref or ticket_ref in candidate):
                    matched = review
                    break

            elapsed = (time.time() - started) * 1000
            if not matched:
                return CopilotQueryResponse(
                    answer=(
                        f"I could not find ticket {ticket_ref} in current reviews. "
                        "Run ticket review first or verify the ticket number."
                    ),
                    sources=[{"filename": "ticket_queue", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.8,
                    processing_time_ms=elapsed,
                )

            failed = matched.get("failed_rules") or self._failed_rule_descriptions(
                [RuleResult(**row) for row in (matched.get("rules") or [])]
            )
            missing = matched.get("missing_documents") or []
            status = str(matched.get("status") or "unknown")
            reason = matched.get("approval_reason") or "No reason captured"
            load_id = matched.get("load_id") or "unknown load"
            ticket_number = matched.get("ticket_number") or ticket_ref

            parts = [f"Ticket {ticket_number} for {load_id} is {status}."]
            if status == TicketStatus.EXCEPTION.value:
                parts.append(f"Flag reason: {reason}.")
                if failed and "Failed checks:" not in str(reason):
                    parts.append(f"Failed checks: {'; '.join(failed[:3])}.")
                if missing and "Missing docs:" not in str(reason):
                    parts.append(f"Missing docs: {', '.join(missing)}.")
                parts.append("Resolve by correcting those checks and re-running ticket review.")
            else:
                parts.append(f"Approval reason: {reason}.")

            return CopilotQueryResponse(
                answer=" ".join(parts),
                sources=[
                    {
                        "filename": matched.get("review_id", "ticket_review"),
                        "document_type": "ticket_review",
                        "similarity": 1.0,
                    }
                ],
                confidence=max(float(matched.get("final_confidence") or 0.9), 0.88),
                processing_time_ms=elapsed,
            )

        if (
            any(
                token in q
                for token in ["flagged", "flag", "exception", "exceptions", "denied", "rejected", "failed", "did not pass", "didn't pass", "not pass"]
            )
            and any(token in q for token in ["ticket", "tickets", "queue", "tkt", "tk"])
        ):
            raw_flagged = [
                row for row in ops_state_store.list_reviews(tenant_id)
                if str(row.get("status", "")).lower() == TicketStatus.EXCEPTION.value
            ]
            flagged: list[dict] = []
            seen_tickets: set[str] = set()
            for row in raw_flagged:
                key = str(row.get("ticket_number") or row.get("review_id") or "")
                if key in seen_tickets:
                    continue
                seen_tickets.add(key)
                flagged.append(row)
                if len(flagged) >= 5:
                    break
            elapsed = (time.time() - started) * 1000
            if not flagged:
                return CopilotQueryResponse(
                    answer="No tickets are currently flagged. Exception queue is clear.",
                    sources=[{"filename": "ticket_queue", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.95,
                    processing_time_ms=elapsed,
                )
            lines = []
            for row in flagged:
                ticket_number = row.get("ticket_number") or "unknown-ticket"
                load_id = row.get("load_id") or "unknown-load"
                reason = row.get("approval_reason") or "unspecified reason"
                lines.append(f"{ticket_number} ({load_id}): {reason}")
            return CopilotQueryResponse(
                answer=f"{len(flagged)} flagged ticket(s): " + " | ".join(lines),
                sources=[{"filename": "ticket_queue", "document_type": "ticket_review", "similarity": 1.0}],
                confidence=0.94,
                processing_time_ms=elapsed,
            )

        load_ticket_status_intent = (
            bool(resolved_load_id)
            and any(token in q for token in ["ticket", "tkt", "tk"])
            and any(token in q for token in ["pass", "passed", "approved", "rejected", "status"])
        )
        if load_ticket_status_intent:
            elapsed = (time.time() - started) * 1000
            review_rows = [
                row
                for row in ops_state_store.list_reviews(tenant_id)
                if str(row.get("load_id") or "").upper() == str(resolved_load_id).upper()
            ]
            if not review_rows:
                return CopilotQueryResponse(
                    answer=f"{resolved_load_id} has no reviewed ticket yet.",
                    sources=[{"filename": "ticket_queue", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.9,
                    processing_time_ms=elapsed,
                )
            latest = review_rows[0]
            ticket_number = latest.get("ticket_number") or "unknown-ticket"
            status = str(latest.get("status") or "unknown")
            reason = latest.get("approval_reason") or "no reason captured"
            answer = (
                f"Latest ticket for {resolved_load_id} is {status}: {ticket_number}. "
                f"Reason: {reason}."
            )
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": latest.get("review_id", "ticket_review"), "document_type": "ticket_review", "similarity": 1.0}],
                confidence=max(float(latest.get("final_confidence") or 0.9), 0.88),
                processing_time_ms=elapsed,
            )

        load_ticket_issue_intent = (
            bool(resolved_load_id)
            and any(token in q for token in ["ticket", "tkt", "tk"])
            and any(token in q for token in ["wrong", "issue", "problem", "deny", "denied", "flag", "failed"])
        )
        if load_ticket_issue_intent:
            elapsed = (time.time() - started) * 1000
            review_rows = [
                row
                for row in ops_state_store.list_reviews(tenant_id)
                if str(row.get("load_id") or "").upper() == str(resolved_load_id).upper()
            ]
            if not review_rows:
                return CopilotQueryResponse(
                    answer=f"{resolved_load_id} has no reviewed ticket yet.",
                    sources=[{"filename": "ticket_queue", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.9,
                    processing_time_ms=elapsed,
                )
            latest = review_rows[0]
            status = str(latest.get("status") or "unknown").lower()
            reason = latest.get("approval_reason") or "no reason captured"
            ticket_number = latest.get("ticket_number") or "unknown-ticket"
            if status in {TicketStatus.EXCEPTION.value}:
                answer = (
                    f"{resolved_load_id} ticket {ticket_number} is flagged. "
                    f"Reason: {reason}."
                )
            else:
                answer = (
                    f"{resolved_load_id} ticket {ticket_number} is {status}; no blocking issue is open. "
                    f"Latest note: {reason}."
                )
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": latest.get("review_id", "ticket_review"), "document_type": "ticket_review", "similarity": 1.0}],
                confidence=max(float(latest.get("final_confidence") or 0.9), 0.88),
                processing_time_ms=elapsed,
            )

        load_route_intent = (
            bool(resolved_load_id)
            and "load" in q
            and any(token in q for token in ["route", "pickup", "dropoff", "location", "drop off", "pick up"])
        )
        if load_route_intent:
            elapsed = (time.time() - started) * 1000
            if resolved_load_id not in load_lookup:
                return CopilotQueryResponse(
                    answer=f"{resolved_load_id} is not in the current dispatch board.",
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.9,
                    processing_time_ms=elapsed,
                )
            row = load_lookup[resolved_load_id]
            assignment = row.get("assignment") or {}
            driver = assignment.get("driver_name") or assignment.get("driver_id") or "unassigned"
            answer = (
                f"{resolved_load_id} route: pickup {row.get('pickup_location') or '-'} -> "
                f"dropoff {row.get('delivery_location') or '-'}. "
                f"Driver: {driver}. Status: {row.get('status')}. Miles: {row.get('planned_miles')}."
            )
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.95,
                processing_time_ms=elapsed,
            )

        load_miles_intent = bool(resolved_load_id) and "load" in q and any(token in q for token in ["mile", "miles"])
        if load_miles_intent:
            elapsed = (time.time() - started) * 1000
            if resolved_load_id not in load_lookup:
                return CopilotQueryResponse(
                    answer=f"{resolved_load_id} is not in the current dispatch board.",
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.9,
                    processing_time_ms=elapsed,
                )
            row = load_lookup[resolved_load_id]
            planned_miles = float(row.get("planned_miles") or 0.0)
            gps_miles = ops_state_store.latest_samsara_miles(tenant_id, resolved_load_id, hours_back=168)
            if gps_miles is None:
                answer = f"{resolved_load_id} has {planned_miles:.1f} planned miles. No GPS miles synced yet."
            else:
                variance = abs(gps_miles - planned_miles)
                answer = (
                    f"{resolved_load_id} has {planned_miles:.1f} planned miles and {gps_miles:.1f} GPS miles "
                    f"(variance {variance:.1f})."
                )
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.95,
                processing_time_ms=elapsed,
            )

        driver_stop_intent = (
            any(token in q for token in ["stop", "stops"])
            and ("driver" in q or any(str(driver.get("name", "")).split(" ")[0].lower() in q for driver in drivers))
        )
        if driver_stop_intent:
            elapsed = (time.time() - started) * 1000
            driver = self._match_driver_from_query(query, drivers)
            if not driver:
                known = ", ".join(str(d.get("name") or "") for d in drivers[:6]) or "no drivers configured"
                return CopilotQueryResponse(
                    answer=f"I couldn't map that to a driver. Try one of: {known}.",
                    sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.84,
                    processing_time_ms=elapsed,
                )

            dloads = self._loads_for_driver(loads, driver)
            load_ids = [str(row.get("load_id") or "") for row in dloads if row.get("load_id")]
            events = ops_state_store.query_samsara_events(tenant_id, load_ids, hours_back=168) if load_ids else []
            total_stops = int(sum(int(row.get("stop_events") or 0) for row in events))
            stops_by_load: Dict[str, int] = {}
            for row in events:
                lid = str(row.get("load_id") or "")
                stops_by_load[lid] = stops_by_load.get(lid, 0) + int(row.get("stop_events") or 0)

            if total_stops <= 0:
                answer = (
                    f"No stop telemetry is available yet for {driver.get('name')}. "
                    f"Assigned loads in scope: {', '.join(load_ids[:5]) or 'none'}."
                )
                confidence = 0.82
            else:
                top = ", ".join(f"{lid}:{count}" for lid, count in list(stops_by_load.items())[:5])
                answer = (
                    f"{driver.get('name')} logged {total_stops} stop event(s) in the last 7 days "
                    f"across {len(load_ids)} load(s). Stops by load: {top}."
                )
                confidence = 0.93
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "samsara_events", "document_type": "system_state", "similarity": 1.0}],
                confidence=confidence,
                processing_time_ms=elapsed,
            )

        load_ownership_intent = (
            bool(resolved_load_id)
            and "load" in q
            and any(
                token in q
                for token in ["who did", "who has", "who took", "assigned to", "who is on", "who owns", "what driver did"]
            )
        )
        if load_ownership_intent:
            elapsed = (time.time() - started) * 1000
            if resolved_load_id not in load_lookup:
                return CopilotQueryResponse(
                    answer=(
                        f"{resolved_load_id} is not in the current dispatch board. "
                        "Refresh board data or verify the load ID."
                    ),
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.92,
                    processing_time_ms=elapsed,
                )
            row = load_lookup[resolved_load_id]
            assignment = row.get("assignment") or {}
            driver = assignment.get("driver_name") or assignment.get("driver_id") or "unassigned"
            return CopilotQueryResponse(
                answer=f"{resolved_load_id} is assigned to {driver}. Current status: {row.get('status')}.",
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.95,
                processing_time_ms=elapsed,
            )

        direct_load_action_intent = bool(resolved_load_id) and re.match(r"^\s*(do|assign|schedule|take|run)\b", q)
        if direct_load_action_intent:
            elapsed = (time.time() - started) * 1000
            if resolved_load_id not in load_lookup:
                return CopilotQueryResponse(
                    answer=f"{resolved_load_id} is not in the current dispatch board.",
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.9,
                    processing_time_ms=elapsed,
                )
            target = load_lookup[resolved_load_id]
            if str(target.get("status", "")).lower() != LoadStatus.PLANNED.value:
                assn = target.get("assignment") or {}
                driver = assn.get("driver_name") or assn.get("driver_id") or "unassigned"
                return CopilotQueryResponse(
                    answer=(
                        f"{resolved_load_id} is already {target.get('status')} and assigned to {driver}. "
                        "No reassignment needed."
                    ),
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.93,
                    processing_time_ms=elapsed,
                )
            available = [d for d in drivers if str(d.get("status", "")).lower() == "available"]
            if not available:
                return CopilotQueryResponse(
                    answer=f"{resolved_load_id} is planned, but no drivers are currently available.",
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.92,
                    processing_time_ms=elapsed,
                )
            assignment = self.assign_load(
                LoadAssignmentRequest(load_id=resolved_load_id, auto=True),
                tenant_id=tenant_id,
                actor="copilot",
            )
            return CopilotQueryResponse(
                answer=(
                    f"Assigned {resolved_load_id} to {assignment.get('driver_name')} "
                    f"({assignment.get('truck_id')})."
                ),
                sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.94,
                processing_time_ms=(time.time() - started) * 1000,
            )

        auto_assign_intent = (
            any(token in q for token in ["auto assign", "assign new loads", "schedule loads", "schedule load"])
            or ("assign" in q and "load" in q)
            or ("schedule" in q and "load" in q)
            or ("drivers are taken" in q and "assign" in q)
        )
        if auto_assign_intent:
            pre_available = [d for d in drivers if str(d.get("status", "")).lower() == "available"]
            assign_limit = 20
            if any(token in q for token in ["assign them", "assign those", "available drivers", "assign all available"]):
                assign_limit = max(1, len(pre_available))
            recycled = 0
            if any(token in q for token in ["finished", "done", "completed", "all my drivers are taken", "drivers are taken"]):
                recycled = self._release_drivers_from_completed_reviews(tenant_id, actor="copilot")
            prefinalized = self._finalize_approved_assigned_loads(tenant_id, actor="copilot")
            assigned_count, errors, assignments = self._auto_assign_planned_loads(tenant_id, actor="copilot", limit=assign_limit)
            quick_reviews: list[Dict[str, Any]] = []
            for row in assignments:
                reviewed = self._run_quick_ticket_review(tenant_id, row.get("load_id", ""), actor="copilot")
                if reviewed:
                    quick_reviews.append(reviewed)
            board = self.dispatch_board(tenant_id)
            board_loads = self._safe_rows(board.get("loads"))
            available = [d for d in board.get("drivers", []) if str(d.get("status", "")).lower() == "available"]
            planned = [l for l in board_loads if str(l.get("status", "")).lower() == LoadStatus.PLANNED.value]
            active = [l for l in board_loads if str(l.get("status", "")).lower() != LoadStatus.DELIVERED.value]
            assigned_open = [
                l for l in board_loads
                if str(l.get("status", "")).lower() in {LoadStatus.ASSIGNED.value, LoadStatus.EN_ROUTE.value}
            ]
            completed = [l for l in board_loads if str(l.get("status", "")).lower() == LoadStatus.DELIVERED.value]
            elapsed = (time.time() - started) * 1000
            if assigned_count > 0:
                assignment_text = ", ".join(
                    f"{row.get('load_id')} -> {row.get('driver_name')} ({row.get('truck_id') or '-'})"
                    for row in assignments
                )
                review_text = ", ".join(
                    f"{row.get('load_id')}/{row.get('ticket_number')}={row.get('status')}"
                    for row in quick_reviews
                )
                return CopilotQueryResponse(
                    answer=(
                        f"I assigned {assigned_count} new load(s). "
                        f"Assignments: {assignment_text or 'none'}. "
                        f"Ticket checks: {review_text or 'none'}. "
                        f"Finalized from passed tickets: {len(prefinalized)}. Released {recycled} completed driver(s). "
                        f"Summary -> active: {len(active)}, assigned: {len(assigned_open)}, complete: {len(completed)}, planned: {len(planned)}, available drivers: {len(available)}."
                    ),
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.93,
                    processing_time_ms=elapsed,
                )
            if errors:
                return CopilotQueryResponse(
                    answer=(
                        f"No new assignments were made. Finalized from passed tickets: {len(prefinalized)}. "
                        f"Released {recycled} driver(s). First blocker: {errors[0]}. "
                        f"Summary -> active: {len(active)}, assigned: {len(assigned_open)}, complete: {len(completed)}, planned: {len(planned)}, available drivers: {len(available)}."
                    ),
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.9,
                    processing_time_ms=elapsed,
                )
            return CopilotQueryResponse(
                answer=(
                    f"No new assignments were made. Finalized from passed tickets: {len(prefinalized)}. "
                    f"Released {recycled} driver(s). "
                    f"Summary -> active: {len(active)}, assigned: {len(assigned_open)}, complete: {len(completed)}, planned: {len(planned)}, available drivers: {len(available)}."
                ),
                sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.9,
                processing_time_ms=elapsed,
            )

        if "schedule" in q and "load" in q:
            available = [d for d in drivers if str(d.get("status", "")).lower() == "available"]
            planned = [l for l in loads if str(l.get("status", "")).lower() == LoadStatus.PLANNED.value]
            elapsed = (time.time() - started) * 1000
            if resolved_load_id and resolved_load_id in load_lookup:
                target = load_lookup[resolved_load_id]
                if not available:
                    return CopilotQueryResponse(
                        answer=f"{resolved_load_id} is ready, but no drivers are currently available to schedule.",
                        sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                        confidence=0.92,
                        processing_time_ms=elapsed,
                    )
                pick = available[0]
                return CopilotQueryResponse(
                    answer=(
                        f"Recommended schedule for {resolved_load_id}: assign {pick.get('name')} ({pick.get('truck_id')}) "
                        f"for {target.get('pickup_location')} -> {target.get('delivery_location')}."
                    ),
                    sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.93,
                    processing_time_ms=elapsed,
                )
            if not planned:
                return CopilotQueryResponse(
                    answer="No planned loads are waiting for scheduling right now.",
                    sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.9,
                    processing_time_ms=elapsed,
                )
            head = ", ".join(l.get("load_id", "") for l in planned[:3])
            return CopilotQueryResponse(
                answer=(
                    f"{len(planned)} loads are in planned status. Next candidates: {head}. "
                    f"Available drivers: {len(available)}."
                ),
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.9,
                processing_time_ms=elapsed,
            )

        if "driver" in q or "drivers" in q or "fleet" in q:
            total = len(drivers)
            available = [d for d in drivers if str(d.get("status", "")).lower() == "available"]
            assigned = [d for d in drivers if str(d.get("status", "")).lower() == "assigned"]
            ask_total_only = ("how many" in q and "available" not in q)
            ask_list_all = any(
                token in q
                for token in ["who are my drivers", "list drivers", "my drivers", "who are the drivers", "driver roster"]
            )
            if ask_total_only:
                answer = f"You have {total} drivers total: {len(available)} available and {len(assigned)} assigned."
            elif ask_list_all:
                all_names = ", ".join(
                    f"{d.get('name')} ({d.get('truck_id', '-')}, {str(d.get('status', 'unknown')).lower()})"
                    for d in drivers[:12]
                ) or "No drivers configured."
                answer = f"Driver roster ({total}): {all_names}"
            elif available:
                names = ", ".join(f"{d.get('name')} ({d.get('truck_id', '-')})" for d in available[:8])
                answer = (
                    f"{len(available)} drivers are available right now: {names}."
                    f" {len(assigned)} currently assigned."
                )
            else:
                all_names = ", ".join(f"{d.get('name')} ({str(d.get('status', 'unknown')).lower()})" for d in drivers[:8])
                if all_names:
                    answer = (
                        f"No drivers are marked available right now. "
                        f"Current driver states: {all_names}."
                    )
                else:
                    answer = "No drivers are configured right now."
            elapsed = (time.time() - started) * 1000
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.96,
                processing_time_ms=elapsed,
            )

        if any(token in q for token in ["how many loads", "active loads", "unassigned", "dispatch board"]):
            active = [row for row in loads if row.get("status") != "delivered"]
            unassigned = [row for row in active if not row.get("assignment")]
            answer = (
                f"Dispatch has {len(active)} active loads, with {len(unassigned)} unassigned and "
                f"{len(active) - len(unassigned)} already assigned."
            )
            elapsed = (time.time() - started) * 1000
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.95,
                processing_time_ms=elapsed,
            )

        if "load " in q:
            if resolved_load_id and resolved_load_id in load_lookup and any(t in q for t in ["status", "assigned", "driver"]):
                row = load_lookup[resolved_load_id]
                assignment = row.get("assignment") or {}
                driver = assignment.get("driver_name") or assignment.get("driver_id") or "unassigned"
                answer = (
                    f"{resolved_load_id} is currently {row.get('status')} and assigned to {driver}. "
                    f"Route: {row.get('pickup_location')} -> {row.get('delivery_location')}."
                )
                elapsed = (time.time() - started) * 1000
                return CopilotQueryResponse(
                    answer=answer,
                    sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.94,
                    processing_time_ms=elapsed,
                )
            if resolved_load_id and any(t in q for t in ["status", "assigned", "driver", "who did", "who has"]):
                elapsed = (time.time() - started) * 1000
                return CopilotQueryResponse(
                    answer=(
                        f"{resolved_load_id} is not in current live state. "
                        "Try refreshing the board or use a recent load ID."
                    ),
                    sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                    confidence=0.9,
                    processing_time_ms=elapsed,
                )

        return None

    def _try_document_fact_answer(
        self,
        query: str,
        tenant_id: str,
        started: float,
        load_id_hint: Optional[str] = None,
    ) -> CopilotQueryResponse | None:
        q = self._normalize_query_for_intents(query)
        wants_broker = "broker" in q
        wants_invoice = "invoice" in q or "inv" in q
        wants_rate = "rate" in q or "rpm" in q
        wants_bol = "bol" in q or "bill of lading" in q or "pro " in q
        if not any([wants_broker, wants_invoice, wants_rate, wants_bol]):
            return None

        load_ids: list[str] = []
        hint = self._extract_load_id(query, explicit=load_id_hint)
        if hint:
            load_ids.append(hint)
        for load_id in self._extract_load_ids(query):
            if load_id not in load_ids:
                load_ids.append(load_id)
        if not load_ids:
            return None

        all_loads = ops_state_store.list_loads(tenant_id)
        resolved_load_ids: list[str] = []
        for load_id in load_ids[:3]:
            resolved = load_id
            if not ops_state_store.get_load(tenant_id, resolved):
                normalized = self._normalize_token(resolved)
                for row in all_loads:
                    candidate = str(row.get("load_id") or "")
                    if candidate and self._normalize_token(candidate) == normalized:
                        resolved = candidate
                        break
            if resolved not in resolved_load_ids:
                resolved_load_ids.append(resolved)

        answer_segments: list[str] = []
        sources: list[Dict[str, Any]] = []
        for load_id in resolved_load_ids:
            docs = document_registry.find_related(load_id, tenant_id=tenant_id)
            if not docs:
                continue

            invoice = None
            rate_conf = None
            bol = None
            for doc in docs:
                kind = str(doc.get("document_type") or "")
                if kind == DocumentType.INVOICE.value and invoice is None:
                    invoice = doc
                elif kind == DocumentType.RATE_CONFIRMATION.value and rate_conf is None:
                    rate_conf = doc
                elif kind == DocumentType.BOL.value and bol is None:
                    bol = doc

            if invoice is None and rate_conf is None and bol is None:
                continue

            load = ops_state_store.get_load(tenant_id, load_id) or {}
            broker = (
                ((rate_conf or {}).get("extracted_data") or {}).get("broker_name")
                or ((invoice or {}).get("extracted_data") or {}).get("broker_name")
                or load.get("broker")
                or "unknown broker"
            )

            segment_parts: list[str] = []
            if wants_broker:
                segment_parts.append(f"broker {broker}")
            if wants_invoice:
                invoice_number = ((invoice or {}).get("extracted_data") or {}).get("invoice_number")
                total = ((invoice or {}).get("extracted_data") or {}).get("total_amount")
                if invoice_number:
                    invoice_text = f"invoice {invoice_number}"
                    if total:
                        invoice_text += f" (${float(total):,.2f})"
                    segment_parts.append(invoice_text)
                else:
                    segment_parts.append("invoice not found")
            if wants_rate:
                rate_value = ((rate_conf or {}).get("extracted_data") or {}).get("rate")
                rpm = ((rate_conf or {}).get("extracted_data") or {}).get("rate_per_mile")
                if rate_value is not None:
                    rate_text = f"rate ${float(rate_value):,.2f}"
                    if rpm is not None:
                        rate_text += f" (${float(rpm):,.2f}/mi)"
                    segment_parts.append(rate_text)
                else:
                    segment_parts.append("rate not found")
            if wants_bol:
                bol_number = ((bol or {}).get("extracted_data") or {}).get("bol_number")
                pro_number = ((bol or {}).get("extracted_data") or {}).get("pro_number")
                bol_text = "BOL/pro not found"
                if bol_number or pro_number:
                    parts = []
                    if bol_number:
                        parts.append(f"BOL {bol_number}")
                    if pro_number:
                        parts.append(f"PRO {pro_number}")
                    bol_text = ", ".join(parts)
                segment_parts.append(bol_text)

            if not segment_parts:
                continue
            answer_segments.append(f"{load_id}: " + ", ".join(segment_parts))
            for doc in (rate_conf, invoice, bol):
                if not doc:
                    continue
                sources.append(
                    {
                        "filename": doc.get("filename"),
                        "document_type": doc.get("document_type"),
                        "similarity": 0.99,
                        "document_id": doc.get("id"),
                    }
                )

        if not answer_segments:
            return None

        elapsed = (time.time() - started) * 1000
        answer_text = " | ".join(answer_segments)
        if len(answer_segments) == 1:
            answer_text = f"Load {answer_text}"
        return CopilotQueryResponse(
            answer=answer_text,
            sources=sources,
            confidence=0.94,
            processing_time_ms=elapsed,
        )

    async def copilot_query(self, request: CopilotQueryRequest, tenant_id: str) -> CopilotQueryResponse:
        started = time.time()
        query = str(request.query or "").strip()
        mode = str(request.mode or "auto").strip().lower()
        session_id = str(request.session_id or "atlas").strip() or "atlas"
        if not query:
            return CopilotQueryResponse(
                answer="Ask me about loads, drivers, tickets, billing, or doc facts for a load ID.",
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=1.0,
                processing_time_ms=(time.time() - started) * 1000,
                route="deterministic",
            )

        if mode == "free_roam":
            free_roam = await self._free_roam_agent.query(
                query=query,
                tenant_id=tenant_id,
                actor="atlas",
                session_id=session_id,
                load_id_hint=request.load_id,
            )
            if free_roam is not None:
                return free_roam
            return CopilotQueryResponse(
                answer=(
                    "Free-roam agent is not available in this process. "
                    "Restart backend after setting OPENROUTER_API_KEY."
                ),
                sources=[{"filename": "runtime", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.2,
                processing_time_ms=(time.time() - started) * 1000,
                route="free_roam_unavailable",
            )
        elif mode == "auto":
            q = query.lower()
            follow_up_intent = any(token in q for token in ["you do it", "do it", "handle it", "resolve it", "assign them"])
            if follow_up_intent:
                free_roam = await self._free_roam_agent.query(
                    query=query,
                    tenant_id=tenant_id,
                    actor="atlas",
                    session_id=session_id,
                    load_id_hint=request.load_id,
                )
                if free_roam is not None:
                    return free_roam

        try:
            state_answer = self._try_ops_state_answer(query, tenant_id=tenant_id, started=started)
            if state_answer is not None:
                return state_answer
        except Exception as exc:
            logger.error("Copilot state-answer failed", error=str(exc), tenant_id=tenant_id, query=query)
            return CopilotQueryResponse(
                answer=(
                    "I can still run dispatch actions, but state lookup failed for that question. "
                    "Try again or refresh demo data."
                ),
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.55,
                processing_time_ms=(time.time() - started) * 1000,
                route="deterministic",
            )

        fact_answer = self._try_document_fact_answer(
            query,
            tenant_id=tenant_id,
            started=started,
            load_id_hint=request.load_id,
        )
        if fact_answer is not None:
            return fact_answer

        system_context = self._build_system_context(tenant_id)
        rag_query = query
        if request.load_id:
            load = ops_state_store.get_load(tenant_id, request.load_id)
            if load:
                rag_query = (
                    f"Load context for {request.load_id}: customer={load.get('customer')}, "
                    f"pickup={load.get('pickup_location')}, dropoff={load.get('delivery_location')}. "
                    f"Question: {query}"
                )

        try:
            response = await rag_engine.query(
                QueryRequest(
                    query=rag_query,
                    include_sources=True,
                    top_k=4,
                    document_types=[
                        DocumentType.RATE_CONFIRMATION,
                        DocumentType.INVOICE,
                        DocumentType.BOL,
                        DocumentType.POD,
                        DocumentType.LUMPER_RECEIPT,
                        DocumentType.EMAIL,
                        DocumentType.POLICY,
                    ],
                ),
                tenant_id=tenant_id,
                extra_context=system_context,
            )
        except Exception as exc:
            logger.error("Copilot document-answer failed", error=str(exc), tenant_id=tenant_id, query=query)
            return CopilotQueryResponse(
                answer=(
                    "Document QA is temporarily unavailable, but live dispatch state is online. "
                    "Try asking about drivers, loads, ticket queue, or billing readiness."
                ),
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.6,
                processing_time_ms=(time.time() - started) * 1000,
                route="deterministic",
            )
        if response.confidence <= 0.2 and not response.sources:
            if mode in {"auto", "free_roam"}:
                free_roam = await self._free_roam_agent.query(
                    query=query,
                    tenant_id=tenant_id,
                    actor="atlas",
                    session_id=session_id,
                    load_id_hint=request.load_id,
                )
                if free_roam is not None:
                    return free_roam
            return CopilotQueryResponse(
                answer=(
                    "I can help with live dispatch actions and load facts. "
                    "Try adding a load ID (example: LOAD00030) or ask about drivers and active loads."
                ),
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.7,
                processing_time_ms=response.processing_time_ms,
                route="deterministic",
            )
        return CopilotQueryResponse(
            answer=response.answer,
            sources=response.sources,
            confidence=response.confidence,
            processing_time_ms=response.processing_time_ms,
            route="deterministic",
        )

    def timeline(self, tenant_id: str, load_id: str) -> Dict[str, Any]:
        load = ops_state_store.get_load(tenant_id, load_id)
        if not load:
            raise KeyError(load_id)

        return {
            "load": load,
            "events": ops_state_store.list_timeline(tenant_id, load_id=load_id),
            "documents": document_registry.find_related(load_id, tenant_id=tenant_id),
        }

    def metrics(self, tenant_id: str) -> OpsMetricsSnapshot:
        snapshot = ops_state_store.metrics_snapshot(tenant_id)
        return OpsMetricsSnapshot(
            active_loads=snapshot["active_loads"],
            delivered_loads=snapshot["delivered_loads"],
            auto_assignment_rate=snapshot["auto_assignment_rate"],
            tickets_reviewed=snapshot["tickets_reviewed"],
            auto_approval_rate=snapshot["auto_approval_rate"],
            exception_rate=snapshot["exception_rate"],
            billing_ready_rate=snapshot["billing_ready_rate"],
            estimated_leakage_recovered_usd=snapshot["estimated_leakage_recovered_usd"],
            avg_review_latency_ms=snapshot["avg_review_latency_ms"],
            p95_review_latency_ms=snapshot["p95_review_latency_ms"],
        )

    def seed_synthetic(self, tenant_id: str, seed: int, loads: int, exception_ratio: float, actor: str) -> Dict[str, Any]:
        if not self.settings.is_demo_mode():
            raise RuntimeError("Synthetic seed is disabled in production mode.")
        ops_state_store.reset_tenant_operational_data(tenant_id)
        ops_state_store.reset_driver_pool(tenant_id)
        result = ops_state_store.seed_synthetic_scenario(
            tenant_id,
            seed=seed,
            loads=loads,
            exception_ratio=exception_ratio,
        )
        ops_state_store.record_timeline_event(
            tenant_id,
            load_id="SYSTEM",
            event_type="synthetic_seed",
            actor=actor,
            details={"seed": seed, "loads": loads, "exception_ratio": exception_ratio},
        )
        return result

    async def seed_demo_pack(self, tenant_id: str, request: DemoPackSeedRequest, actor: str) -> Dict[str, Any]:
        if not self.settings.is_demo_mode():
            raise RuntimeError("Synthetic seed is disabled in production mode.")

        seeded = self.seed_synthetic(
            tenant_id=tenant_id,
            seed=request.seed,
            loads=request.loads,
            exception_ratio=request.include_exceptions_ratio,
            actor=actor,
        )
        load_ids = list(seeded.get("load_ids", []))
        docs_per_load = max(1, min(int(request.docs_per_load), 5))

        drivers = ops_state_store.list_drivers(tenant_id)
        docs_to_index: list[tuple[Document, list[tuple[str, dict]]]] = []
        created_docs = 0
        indexed_docs = 0
        notes: list[str] = []

        for idx, load_id in enumerate(load_ids):
            load = ops_state_store.get_load(tenant_id, load_id) or {}
            driver = drivers[idx % len(drivers)] if drivers else {}
            broker = str(load.get("broker") or "Coyote Logistics, LLC").strip()
            customer = str(load.get("customer") or "A-1 BLOCK CORPORATION").strip()
            miles = round(float(load.get("planned_miles") or 0.0), 2)
            total_rate = round(float(load.get("rate_total") or 0.0), 2)
            rate_per_mile = round(total_rate / max(miles, 1.0), 2)
            numeric = 100000 + ((idx + request.seed) * 137) % 899999
            rate_conf = f"RC{numeric:06d}"
            bol = f"BOL{(numeric + 37) % 999999:06d}"
            pro = f"PRO{(numeric + 71) % 999999:06d}"
            invoice = f"INV-2026-{load_id}"

            docs_payload = [
                (
                    DocumentType.RATE_CONFIRMATION,
                    f"RateConf_{rate_conf}_{broker}.pdf",
                    {
                        "load_number": load_id,
                        "broker_name": broker,
                        "rate_conf_number": rate_conf,
                        "rate": total_rate,
                        "rate_per_mile": rate_per_mile,
                        "miles": miles,
                        "pickup_location": load.get("pickup_location"),
                        "delivery_location": load.get("delivery_location"),
                        "equipment_type": load.get("equipment_type") or "bulk",
                    },
                    (
                        f"Rate Confirmation {rate_conf}\n"
                        f"Load: {load_id}\nBroker: {broker}\nCustomer: {customer}\n"
                        f"Pickup: {load.get('pickup_location')} | Delivery: {load.get('delivery_location')}\n"
                        f"Miles: {miles}\nRate: ${total_rate:,.2f}\nRate per mile: ${rate_per_mile:,.2f}\n"
                        f"Target rate quality: lane verified for detention and billing."
                    ),
                ),
                (
                    DocumentType.INVOICE,
                    f"Invoice_{invoice}_{broker}.pdf",
                    {
                        "invoice_number": invoice,
                        "load_number": load_id,
                        "broker_name": broker,
                        "total_amount": total_rate,
                    },
                    (
                        f"Invoice {invoice}\nLoad: {load_id}\nBroker: {broker}\n"
                        f"Amount Due: ${total_rate:,.2f}\n"
                        f"Ticket Review: approved for billing pipeline.\n"
                        f"Generated by SHAMS demo pack."
                    ),
                ),
                (
                    DocumentType.BOL,
                    f"BOL_{bol}_{load_id}.pdf",
                    {
                        "bol_number": bol,
                        "load_number": load_id,
                        "pro_number": pro,
                        "driver_name": driver.get("name") or "Carlos Rodriguez",
                        "equipment_type": load.get("equipment_type") or "bulk",
                        "weight": "42000 lb",
                        "reference_number": f"REF-{numeric:06d}",
                    },
                    (
                        f"Bill of Lading {bol}\nLoad: {load_id}\nPRO: {pro}\n"
                        f"Driver: {driver.get('name') or 'Carlos Rodriguez'}\n"
                        f"Equipment: {load.get('equipment_type') or 'bulk'}\n"
                        f"Shipper: {customer}\nConsignee: {load.get('delivery_location')}\n"
                        "Weight: 42000 lb\n"
                    ),
                ),
                (
                    DocumentType.POD,
                    f"POD_{pro}_{load_id}.pdf",
                    {
                        "load_number": load_id,
                        "pro_number": pro,
                        "bol_number": bol,
                        "delivered_to": customer,
                        "signed_for_by": "Dock Supervisor",
                        "delivery_date": "2026-02-16",
                    },
                    (
                        f"Proof of Delivery\nLoad: {load_id}\nPRO: {pro}\nBOL: {bol}\n"
                        f"Delivered to: {customer}\nSigned by: Dock Supervisor\nCondition: Good\n"
                    ),
                ),
                (
                    DocumentType.LUMPER_RECEIPT,
                    f"Lumper_{load_id}_{numeric:06d}.pdf",
                    {
                        "receipt_number": f"LMP-{numeric:06d}",
                        "load_number": load_id,
                        "pro_number": pro,
                        "total_fee": 165.0,
                    },
                    (
                        f"Lumper Receipt LMP-{numeric:06d}\nLoad: {load_id}\nPRO: {pro}\n"
                        "Service: Unloading\nTotal fee: $165.00\n"
                    ),
                ),
            ]

            now = datetime.now(timezone.utc)
            for ordinal, (doc_type, filename, extracted_data, raw_text) in enumerate(docs_payload[:docs_per_load]):
                document = Document(
                    id=f"demo-{load_id.lower()}-{doc_type.value}-{ordinal}",
                    filename=filename,
                    document_type=doc_type,
                    status=DocumentStatus.PROCESSED,
                    raw_text=raw_text,
                    extracted_data=extracted_data,
                    metadata={"tenant_id": tenant_id, "source": "synthetic_demo_pack"},
                    created_at=now,
                    processed_at=now,
                )
                document_registry.upsert(document, tenant_id=tenant_id)
                if request.index_documents:
                    chunks = document_processor.chunk_text(document.raw_text, chunk_size=600, chunk_overlap=80)
                    if chunks:
                        docs_to_index.append((document, chunks))
                created_docs += 1

        if docs_to_index and request.index_documents:
            try:
                all_chunks = [chunk_text for _, chunks in docs_to_index for chunk_text, _ in chunks]
                embeddings = await embedding_service.embed_batch(all_chunks)
                cursor = 0
                bulk_payload: list[tuple[Document, list[tuple[str, dict]], list[list[float]]]] = []
                for document, chunks in docs_to_index:
                    span = len(chunks)
                    bulk_payload.append((document, chunks, embeddings[cursor: cursor + span]))
                    cursor += span
                indexed_docs = await vector_store.add_documents_bulk(bulk_payload, tenant_id=tenant_id)
            except Exception as exc:
                logger.warning("Demo pack seeded without vector indexing", error=str(exc))
                notes.append(f"Vector indexing skipped: {exc}")
        elif not request.index_documents:
            notes.append("Vector indexing skipped by request for faster demo preload.")

        ops_state_store.record_timeline_event(
            tenant_id,
            load_id="SYSTEM",
            event_type="demo_pack_seeded",
            actor=actor,
            details={
                "seed": request.seed,
                "loads": request.loads,
                "docs_per_load": docs_per_load,
                "documents_created": created_docs,
                "documents_indexed": indexed_docs,
            },
        )

        return {
            "loads_created": seeded.get("loads_created", 0),
            "documents_created": created_docs,
            "documents_indexed": indexed_docs,
            "load_ids": load_ids,
            "notes": notes,
        }

    async def run_autonomy_cycle(
        self,
        request: AutonomyRunRequest,
        tenant_id: str,
        actor: str,
    ) -> AutonomyRunResponse:
        """Run one deterministic autonomous operations cycle."""
        loads = ops_state_store.list_loads(tenant_id)[: request.max_loads]
        reviews_by_load: dict[str, dict[str, Any]] = {}
        for row in ops_state_store.list_reviews(tenant_id):
            load_id = row.get("load_id")
            if not load_id:
                continue
            if load_id not in reviews_by_load:
                reviews_by_load[load_id] = row

        assigned = 0
        reviewed = 0
        exported = 0
        errors: list[str] = []

        for load in loads:
            load_id = str(load.get("load_id", "")).strip()
            if not load_id:
                continue

            status = str(load.get("status", "planned"))
            has_assignment = bool(load.get("assignment"))
            if status == LoadStatus.PLANNED.value and not has_assignment:
                try:
                    assignment = ops_state_store.auto_assign_load(tenant_id, load_id)
                    assigned += 1
                    ops_state_store.record_timeline_event(
                        tenant_id,
                        load_id,
                        event_type="load_assigned",
                        actor=actor,
                        details={"mode": "autonomous", **assignment},
                    )
                    load = ops_state_store.get_load(tenant_id, load_id) or load
                except Exception as exc:
                    errors.append(f"{load_id}: assignment failed: {exc}")
                    continue

            if load_id not in reviews_by_load:
                try:
                    result = await self.review_ticket(
                        TicketReviewRequest(load_id=load_id),
                        tenant_id=tenant_id,
                        actor=actor,
                    )
                    reviews_by_load[load_id] = result.model_dump(mode="json")
                    reviewed += 1
                except Exception as exc:
                    errors.append(f"{load_id}: review failed: {exc}")

        if request.include_exports:
            existing_exports = {row.get("load_id") for row in ops_state_store.list_exports(tenant_id)}
            for billing in ops_state_store.list_billing(tenant_id):
                load_id = billing.get("load_id")
                if not load_id or load_id in existing_exports:
                    continue
                if not bool(billing.get("billing_ready")):
                    continue
                try:
                    self.create_mcleod_export(load_id, tenant_id=tenant_id, actor=actor)
                    exported += 1
                    existing_exports.add(load_id)
                except Exception as exc:
                    errors.append(f"{load_id}: export failed: {exc}")

        ops_state_store.record_timeline_event(
            tenant_id,
            load_id="SYSTEM",
            event_type="autonomy_cycle",
            actor=actor,
            details={
                "scanned_loads": len(loads),
                "assigned_loads": assigned,
                "reviewed_loads": reviewed,
                "exports_generated": exported,
                "errors": len(errors),
            },
        )

        return AutonomyRunResponse(
            scanned_loads=len(loads),
            assigned_loads=assigned,
            reviewed_loads=reviewed,
            exports_generated=exported,
            errors=errors,
        )


ops_engine = OpsEngine()
