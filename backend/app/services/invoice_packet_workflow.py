"""
Invoice Packet Assembly Workflow.

Production-oriented workflow for assembling billing packets by load:
1. Locate related documents from persistent registry
2. Match required document types
3. Cross-check identifiers and amounts
4. Flag missing pieces and risk conditions
5. Return audit-friendly packet with next actions
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import re
from typing import Any, Dict, List, Optional

from app.models.workflows import (
    DocumentMatch,
    InvoiceBaselineConfig,
    InvoicePacket,
    InvoicePacketMetrics,
    InvoicePacketRequest,
    InvoiceRoiEstimate,
    WorkflowStatus,
)
from app.services.document_registry import document_registry
from app.core.logging import logger


class InvoicePacketWorkflow:
    """Automates invoice packet assembly with deterministic validation logic."""

    REQUIRED_DOC_TYPES = ("rate_confirmation", "bill_of_lading", "proof_of_delivery")

    def __init__(self) -> None:
        self._default_baseline = InvoiceBaselineConfig(
            avg_manual_minutes_per_invoice=15.0,
            monthly_invoice_volume=200,
            kickback_rate=0.08,
            avg_rework_minutes=20.0,
            labor_rate_per_hour=24.44,
        )
        self._tenant_baselines: Dict[str, InvoiceBaselineConfig] = {}
        self._tenant_metrics: Dict[str, Dict[str, float]] = {}

    def _metrics_for(self, tenant_id: str) -> Dict[str, float]:
        bucket = self._tenant_metrics.get(tenant_id)
        if bucket is None:
            bucket = {
                "packets_generated": 0,
                "total_time_seconds": 0.0,
                "missing_documents": 0,
                "validation_errors": 0,
            }
            self._tenant_metrics[tenant_id] = bucket
        return bucket

    def _baseline_for(self, tenant_id: str) -> InvoiceBaselineConfig:
        return self._tenant_baselines.get(tenant_id, self._default_baseline)

    @staticmethod
    def _normalize_identifier(value: str | None) -> str:
        if not value:
            return ""
        return re.sub(r"[^A-Z0-9]", "", value.upper())

    def _score_document_match(self, record: Dict[str, Any], load_id: str) -> float:
        target = self._normalize_identifier(load_id)
        filename = self._normalize_identifier(record.get("filename", ""))
        load_ids = [self._normalize_identifier(item) for item in record.get("load_ids", [])]
        score = 0.25

        if target in load_ids:
            score += 0.55
        if target and target in filename:
            score += 0.1
        if record.get("processed_at"):
            score += 0.05
        if record.get("status") == "processed":
            score += 0.05

        return max(0.0, min(0.99, score))

    def _match_to_packet_field(self, record: Dict[str, Any], load_id: str) -> DocumentMatch:
        return DocumentMatch(
            document_id=record.get("id", "unknown"),
            document_type=record.get("document_type", "unknown"),
            filename=record.get("filename", "unknown"),
            confidence=self._score_document_match(record, load_id),
            matched_field="load_id",
        )

    def _pick_best(self, docs: List[Dict[str, Any]], load_id: str) -> Optional[Dict[str, Any]]:
        if not docs:
            return None
        ranked = sorted(
            docs,
            key=lambda item: self._score_document_match(item, load_id),
            reverse=True,
        )
        return ranked[0]

    def _find_related_records(self, request: InvoicePacketRequest, tenant_id: str) -> List[Dict[str, Any]]:
        related: List[Dict[str, Any]] = []
        seen = set()

        for document_id in request.document_ids:
            record = document_registry.get(document_id, tenant_id=tenant_id)
            if record and document_id not in seen:
                related.append(record)
                seen.add(document_id)

        if request.auto_find_documents:
            for record in document_registry.find_related(request.load_id, tenant_id=tenant_id):
                record_id = record.get("id")
                if record_id and record_id not in seen:
                    related.append(record)
                    seen.add(record_id)

        return related

    def _get_identifier(self, record: Optional[Dict[str, Any]], key: str) -> Optional[str]:
        if not record:
            return None
        extracted = record.get("extracted_data", {}) or {}
        value = extracted.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
        return None

    def _get_float(self, record: Optional[Dict[str, Any]], key: str) -> Optional[float]:
        if not record:
            return None
        extracted = record.get("extracted_data", {}) or {}
        value = extracted.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _requires_lumper(self, rate_con: Optional[Dict[str, Any]], invoice: Optional[Dict[str, Any]]) -> bool:
        fields = []
        for record in (rate_con, invoice):
            if not record:
                continue
            extracted = record.get("extracted_data", {}) or {}
            fields.append(str(extracted.get("accessorials", "")))
            fields.append(str(extracted.get("line_items", "")))
            fields.append(record.get("raw_text", ""))
        haystack = " ".join(fields).lower()
        return "lumper" in haystack or "unloading fee" in haystack

    def _set_packet_documents(
        self,
        packet: InvoicePacket,
        request: InvoicePacketRequest,
        grouped: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        selected = {
            "rate_confirmation": self._pick_best(grouped["rate_confirmation"], request.load_id),
            "invoice": self._pick_best(grouped["invoice"], request.load_id),
            "bill_of_lading": self._pick_best(grouped["bill_of_lading"], request.load_id),
            "proof_of_delivery": self._pick_best(grouped["proof_of_delivery"], request.load_id),
            "lumper_receipt": self._pick_best(grouped["lumper_receipt"], request.load_id),
        }

        if selected["rate_confirmation"]:
            packet.rate_confirmation = self._match_to_packet_field(selected["rate_confirmation"], request.load_id)
        if selected["invoice"]:
            packet.invoice = self._match_to_packet_field(selected["invoice"], request.load_id)
        if selected["bill_of_lading"]:
            packet.bol = self._match_to_packet_field(selected["bill_of_lading"], request.load_id)
        if selected["proof_of_delivery"]:
            packet.pod = self._match_to_packet_field(selected["proof_of_delivery"], request.load_id)
        if selected["lumper_receipt"]:
            packet.lumper_receipt = self._match_to_packet_field(selected["lumper_receipt"], request.load_id)

        return selected

    def _validate_consistency(self, packet: InvoicePacket, selected: Dict[str, Optional[Dict[str, Any]]]) -> None:
        rate_con = selected["rate_confirmation"]
        invoice = selected["invoice"]
        bol = selected["bill_of_lading"]
        pod = selected["proof_of_delivery"]
        lumper = selected["lumper_receipt"]

        # Validate required document presence.
        if not packet.rate_confirmation:
            packet.missing_documents.append("rate_confirmation")
        if not packet.bol:
            packet.missing_documents.append("bol")
        if not packet.pod:
            packet.missing_documents.append("pod")

        if not packet.invoice:
            packet.next_actions.append("Create invoice document before submitting packet.")

        # Load ID consistency.
        load_ids = {
            "rate_confirmation": self._get_identifier(rate_con, "load_number"),
            "invoice": self._get_identifier(invoice, "load_number"),
            "bill_of_lading": self._get_identifier(bol, "load_number"),
            "proof_of_delivery": self._get_identifier(pod, "load_number"),
            "lumper_receipt": self._get_identifier(lumper, "load_number"),
        }
        normalized_values = {
            key: self._normalize_identifier(value) for key, value in load_ids.items() if value
        }
        if normalized_values:
            expected = self._normalize_identifier(packet.load_id)
            for source, value in normalized_values.items():
                if expected and value != expected:
                    packet.validation_errors.append(
                        f"{source} load number does not match requested load {packet.load_id}"
                    )

        # BOL consistency across docs.
        bol_numbers = {
            "rate_confirmation": self._get_identifier(rate_con, "bol_number"),
            "invoice": self._get_identifier(invoice, "bol_number"),
            "proof_of_delivery": self._get_identifier(pod, "bol_number"),
            "lumper_receipt": self._get_identifier(lumper, "bol_number"),
            "bill_of_lading": self._get_identifier(bol, "bol_number"),
        }
        normalized_bol_numbers = {
            source: self._normalize_identifier(value)
            for source, value in bol_numbers.items() if value
        }
        unique_bols = {value for value in normalized_bol_numbers.values() if value}
        if len(unique_bols) > 1:
            packet.validation_errors.append("BOL numbers are inconsistent across packet documents.")

        # Pro number consistency.
        pro_numbers = {
            "rate_confirmation": self._get_identifier(rate_con, "pro_number"),
            "invoice": self._get_identifier(invoice, "pro_number"),
            "proof_of_delivery": self._get_identifier(pod, "pro_number"),
            "lumper_receipt": self._get_identifier(lumper, "pro_number"),
            "bill_of_lading": self._get_identifier(bol, "pro_number"),
        }
        normalized_pro_numbers = {
            source: self._normalize_identifier(value)
            for source, value in pro_numbers.items() if value
        }
        unique_pros = {value for value in normalized_pro_numbers.values() if value}
        if len(unique_pros) > 1:
            packet.validation_errors.append("PRO numbers are inconsistent across packet documents.")

        # Amount consistency check (only warning).
        rate_amount = self._get_float(rate_con, "rate")
        invoice_amount = self._get_float(invoice, "total_amount")
        if rate_amount is not None:
            packet.invoice_amount = round(rate_amount, 2)
        if invoice_amount is not None and rate_amount is not None:
            variance = abs(invoice_amount - rate_amount)
            if variance > 5.0:
                packet.warnings.append(
                    f"Invoice total (${invoice_amount:.2f}) differs from rate confirmation (${rate_amount:.2f})."
                )

        if self._requires_lumper(rate_con, invoice) and not packet.lumper_receipt:
            packet.warnings.append("Load appears to include lumper charges but no lumper receipt is attached.")
            packet.next_actions.append("Request lumper receipt before submission to avoid kickback risk.")

        pod_condition = (pod or {}).get("extracted_data", {}).get("condition")
        if isinstance(pod_condition, str):
            lowered = pod_condition.lower()
            if "n good n damaged n shortage" not in lowered and ("damaged" in lowered or "shortage" in lowered):
                packet.warnings.append("POD notes potential damage/shortage. Confirm claim handling before invoicing.")

    def _populate_summary(self, packet: InvoicePacket, selected: Dict[str, Optional[Dict[str, Any]]]) -> None:
        rate_con = selected["rate_confirmation"]
        invoice = selected["invoice"]
        bol = selected["bill_of_lading"]
        pod = selected["proof_of_delivery"]
        lumper = selected["lumper_receipt"]

        rate_data = (rate_con or {}).get("extracted_data", {}) or {}
        invoice_data = (invoice or {}).get("extracted_data", {}) or {}

        packet.broker_name = rate_data.get("broker_name") or invoice_data.get("broker_name")
        packet.broker_mc = rate_data.get("broker_mc") or invoice_data.get("broker_mc")

        packet.load_details = {
            "requested_load_id": packet.load_id,
            "matched_document_ids": [
                record.get("id")
                for record in (rate_con, invoice, bol, pod, lumper)
                if record and record.get("id")
            ],
            "rate_conf_number": rate_data.get("rate_conf_number") or rate_data.get("confirmation_number"),
            "bol_number": (
                (bol or {}).get("extracted_data", {}).get("bol_number")
                or rate_data.get("bol_number")
                or invoice_data.get("bol_number")
            ),
            "pro_number": (
                (bol or {}).get("extracted_data", {}).get("pro_number")
                or (pod or {}).get("extracted_data", {}).get("pro_number")
                or invoice_data.get("pro_number")
            ),
            "equipment_type": rate_data.get("equipment_type"),
            "pickup_location": rate_data.get("pickup_location"),
            "delivery_location": rate_data.get("delivery_location"),
            "delivery_date": (pod or {}).get("extracted_data", {}).get("delivery_date"),
            "lumper_fee": (lumper or {}).get("extracted_data", {}).get("total_fee"),
        }

    async def assemble_packet(self, request: InvoicePacketRequest, tenant_id: str = "demo") -> InvoicePacket:
        import time

        start_time = time.time()
        packet = InvoicePacket(load_id=request.load_id, status=WorkflowStatus.IN_PROGRESS)

        logger.info("Starting invoice packet assembly", tenant_id=tenant_id, load_id=request.load_id)
        records = self._find_related_records(request, tenant_id=tenant_id)

        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for record in records:
            doc_type = record.get("document_type", "other")
            grouped[doc_type].append(record)

        selected = self._set_packet_documents(packet, request, grouped)
        self._populate_summary(packet, selected)
        self._validate_consistency(packet, selected)

        if packet.validation_errors or packet.missing_documents:
            packet.status = WorkflowStatus.NEEDS_REVIEW
        else:
            packet.status = WorkflowStatus.COMPLETED

        if packet.missing_documents:
            packet.next_actions.extend(
                [f"Attach missing {doc_type.replace('_', ' ')} document." for doc_type in packet.missing_documents]
            )
        if packet.validation_errors:
            packet.next_actions.extend([f"Resolve validation: {error}" for error in packet.validation_errors])
        if not packet.next_actions and packet.status == WorkflowStatus.COMPLETED:
            packet.next_actions.append("Submit packet to broker AP contact.")

        elapsed = time.time() - start_time
        metrics = self._metrics_for(tenant_id)
        metrics["packets_generated"] += 1
        metrics["total_time_seconds"] += elapsed
        if packet.missing_documents:
            metrics["missing_documents"] += 1
        if packet.validation_errors:
            metrics["validation_errors"] += 1

        logger.info(
            "Invoice packet assembly complete",
            tenant_id=tenant_id,
            load_id=request.load_id,
            status=packet.status.value,
            related_docs=len(records),
            missing=len(packet.missing_documents),
            errors=len(packet.validation_errors),
            elapsed_seconds=elapsed,
        )
        return packet

    def set_baseline(self, config: InvoiceBaselineConfig, tenant_id: str = "demo") -> None:
        self._tenant_baselines[tenant_id] = config

    def get_roi_estimate(self, tenant_id: str = "demo") -> InvoiceRoiEstimate:
        baseline = self._baseline_for(tenant_id)
        metrics = self.get_metrics(tenant_id=tenant_id)
        minutes_saved_per_invoice = max(
            0.0,
            baseline.avg_manual_minutes_per_invoice - metrics.automated_average_minutes,
        )
        monthly_labor_savings = (
            minutes_saved_per_invoice
            * baseline.monthly_invoice_volume
            / 60.0
            * baseline.labor_rate_per_hour
        )

        # Missing-doc rate is used as a conservative proxy for avoidable kickbacks.
        kickback_rate_after = min(baseline.kickback_rate, metrics.missing_document_rate)
        monthly_kickbacks_avoided = (
            baseline.kickback_rate - kickback_rate_after
        ) * baseline.monthly_invoice_volume
        monthly_rework_savings = (
            monthly_kickbacks_avoided
            * baseline.avg_rework_minutes
            / 60.0
            * baseline.labor_rate_per_hour
        )

        return InvoiceRoiEstimate(
            baseline=baseline,
            observed_average_seconds=metrics.average_time_seconds,
            observed_missing_document_rate=metrics.missing_document_rate,
            minutes_saved_per_invoice=minutes_saved_per_invoice,
            monthly_labor_savings=round(monthly_labor_savings, 2),
            monthly_kickbacks_avoided=round(monthly_kickbacks_avoided, 2),
            monthly_rework_savings=round(monthly_rework_savings, 2),
            total_monthly_value=round(monthly_labor_savings + monthly_rework_savings, 2),
        )

    def get_open_loads(self, limit: int = 50, tenant_id: str = "demo") -> List[Dict[str, Any]]:
        """List loads with packet readiness summary for dispatch/AP queues."""
        grouped: Dict[str, Dict[str, Any]] = {}
        for record in document_registry.list(tenant_id=tenant_id, limit=2000):
            for load_id in record.get("load_ids", []):
                bucket = grouped.setdefault(
                    load_id,
                    {
                        "load_id": load_id,
                        "document_types": set(),
                        "document_ids": [],
                    },
                )
                bucket["document_types"].add(record.get("document_type", "other"))
                if record.get("id"):
                    bucket["document_ids"].append(record["id"])

        summaries = []
        for load_id, payload in grouped.items():
            document_types = payload["document_types"]
            missing_required = [
                doc_type for doc_type in self.REQUIRED_DOC_TYPES if doc_type not in document_types
            ]
            status = "ready" if not missing_required else "needs_docs"
            summaries.append(
                {
                    "load_id": load_id,
                    "status": status,
                    "missing_required": missing_required,
                    "document_count": len(payload["document_ids"]),
                }
            )

        summaries.sort(key=lambda item: (item["status"], item["load_id"]))
        return summaries[: max(1, min(limit, 500))]

    def get_metrics(self, tenant_id: str = "demo") -> InvoicePacketMetrics:
        metrics = self._metrics_for(tenant_id)
        baseline = self._baseline_for(tenant_id)
        total = int(metrics["packets_generated"])
        if total == 0:
            return InvoicePacketMetrics(
                total_packets_generated=0,
                average_time_seconds=0.0,
                missing_document_rate=0.0,
                rejection_rate=0.0,
                time_saved_vs_manual=0.0,
                manual_average_minutes=baseline.avg_manual_minutes_per_invoice,
                automated_average_minutes=0.0,
                estimated_monthly_labor_savings=0.0,
                estimated_monthly_rework_savings=0.0,
            )

        avg_time = metrics["total_time_seconds"] / total
        missing_rate = metrics["missing_documents"] / total
        manual_seconds = baseline.avg_manual_minutes_per_invoice * 60.0
        time_saved_per_packet_hours = max(0.0, (manual_seconds - avg_time) / 3600.0)

        minutes_saved = max(0.0, baseline.avg_manual_minutes_per_invoice - (avg_time / 60.0))
        monthly_labor_savings = (
            minutes_saved
            * baseline.monthly_invoice_volume
            / 60.0
            * baseline.labor_rate_per_hour
        )
        kickbacks_avoided = (
            baseline.kickback_rate - min(baseline.kickback_rate, missing_rate)
        ) * baseline.monthly_invoice_volume
        monthly_rework_savings = (
            kickbacks_avoided
            * baseline.avg_rework_minutes
            / 60.0
            * baseline.labor_rate_per_hour
        )

        return InvoicePacketMetrics(
            total_packets_generated=total,
            average_time_seconds=avg_time,
            missing_document_rate=missing_rate,
            rejection_rate=missing_rate,
            time_saved_vs_manual=time_saved_per_packet_hours,
            manual_average_minutes=baseline.avg_manual_minutes_per_invoice,
            automated_average_minutes=avg_time / 60.0,
            estimated_monthly_labor_savings=round(monthly_labor_savings, 2),
            estimated_monthly_rework_savings=round(monthly_rework_savings, 2),
        )

    @property
    def baseline(self) -> InvoiceBaselineConfig:
        """Backward-compatible baseline accessor for default tenant."""
        return self._baseline_for("demo")


invoice_packet_workflow = InvoicePacketWorkflow()
