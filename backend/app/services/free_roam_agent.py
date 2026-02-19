"""LLM-driven free-roam copilot that can execute SHAMS ops tools."""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict, deque
from datetime import datetime, time as dt_time, timedelta, timezone
from threading import RLock
from typing import Any, Deque, Dict, List, Optional

from app.core.config import get_settings
from app.core.logging import logger
from app.models.document import DocumentType, QueryRequest
from app.models.ops import (
    CopilotQueryResponse,
    LoadAssignmentRequest,
    LoadCreateRequest,
    LoadStatus,
    LoadStatusTransitionRequest,
    TicketDecisionRequest,
    TicketReviewRequest,
)
from app.services.document_registry import document_registry
from app.services.microsoft_graph import microsoft_graph_service
from app.services.ops_state import ops_state_store
from app.services.rag_engine import rag_engine

try:
    from openai import AsyncOpenAI

    HAS_OPENAI = True
except Exception:  # pragma: no cover - import guard
    HAS_OPENAI = False
    AsyncOpenAI = None


class FreeRoamAgent:
    """OpenRouter-backed copilot with tool calls over the existing ops engine."""

    LOAD_ID_PATTERN = re.compile(r"\bLOAD[-_ ]?(\d{3,}[A-Z0-9]*)\b", re.IGNORECASE)
    TICKET_PATTERN = re.compile(r"\b(?:ticket|tkt|tk)\s*#?\s*[:\-]?\s*([A-Z0-9\-]{5,})\b", re.IGNORECASE)

    def __init__(self, ops_engine: Any) -> None:
        self.settings = get_settings()
        self.ops_engine = ops_engine
        self._lock = RLock()
        self._memory: Dict[str, Deque[Dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max(4, int(self.settings.free_roam_memory_turns)))
        )
        self._enabled = bool(self.settings.free_roam_enabled)
        self._client: Any = None

        key = (self.settings.openrouter_api_key or "").strip()
        if self._enabled and key and HAS_OPENAI:
            self._client = AsyncOpenAI(
                api_key=key,
                base_url=self.settings.openrouter_base_url,
                timeout=float(self.settings.openrouter_timeout_seconds),
            )
        elif self._enabled:
            logger.info("Free-roam copilot disabled (missing OPENROUTER_API_KEY or openai package).")

    def is_enabled(self) -> bool:
        return self._enabled and self._client is not None

    @classmethod
    def _normalize_load_id(cls, candidate: Optional[str]) -> str:
        text = str(candidate or "").strip().upper().replace("-", "").replace("_", "")
        if not text:
            return ""
        if not text.startswith("LOAD"):
            text = f"LOAD{text}"
        suffix = text[4:]
        match = re.fullmatch(r"0*(\d+)([A-Z0-9]*)", suffix)
        if not match:
            return text
        return f"LOAD{str(int(match.group(1))).zfill(5)}{match.group(2)}"

    @classmethod
    def _normalize_ticket(cls, value: Optional[str]) -> str:
        return re.sub(r"[^A-Z0-9\-]", "", str(value or "").upper())

    @classmethod
    def _extract_load_id(cls, text: str) -> str:
        match = cls.LOAD_ID_PATTERN.search(text or "")
        if not match:
            return ""
        return cls._normalize_load_id(f"LOAD{match.group(1)}")

    @classmethod
    def _extract_ticket(cls, text: str) -> str:
        match = cls.TICKET_PATTERN.search(text or "")
        if not match:
            return ""
        return cls._normalize_ticket(match.group(1))

    def _memory_key(self, tenant_id: str, actor: str, session_id: str) -> str:
        return f"{tenant_id}:{actor}:{session_id or 'atlas'}"

    def _history(self, tenant_id: str, actor: str, session_id: str) -> List[Dict[str, str]]:
        key = self._memory_key(tenant_id, actor, session_id)
        with self._lock:
            return list(self._memory[key])

    def _remember(self, tenant_id: str, actor: str, session_id: str, role: str, content: str) -> None:
        if not content:
            return
        key = self._memory_key(tenant_id, actor, session_id)
        with self._lock:
            self._memory[key].append({"role": role, "content": content})

    def _tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "dispatch_summary",
                    "description": "Get current load/driver state and status counts.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "assign_available_drivers",
                    "description": "Assign available drivers to planned loads and run quick ticket checks.",
                    "parameters": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 40}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "assign_specific_load",
                    "description": "Assign one specific load to the next available driver.",
                    "parameters": {
                        "type": "object",
                        "properties": {"load_id": {"type": "string"}},
                        "required": ["load_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_load",
                    "description": "Create a new load in dispatch.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "customer": {"type": "string"},
                            "broker": {"type": "string"},
                            "pickup_location": {"type": "string"},
                            "delivery_location": {"type": "string"},
                            "planned_miles": {"type": "number"},
                            "rate_total": {"type": "number"},
                            "zone": {"type": "string"},
                        },
                        "required": ["customer", "pickup_location", "delivery_location"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_load_status",
                    "description": "Transition load lifecycle status (planned, assigned, en_route, delivered, blocked).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "load_id": {"type": "string"},
                            "status": {"type": "string"},
                        },
                        "required": ["load_id", "status"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "review_ticket",
                    "description": "Run autonomous ticket review for a load.",
                    "parameters": {
                        "type": "object",
                        "properties": {"load_id": {"type": "string"}},
                        "required": ["load_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "resolve_ticket",
                    "description": "Resolve an exception ticket by ticket number or review id.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ticket_number": {"type": "string"},
                            "review_id": {"type": "string"},
                            "note": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "resolve_flagged_tickets",
                    "description": "Resolve up to N currently flagged tickets in queue.",
                    "parameters": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ticket_status",
                    "description": "Get ticket status and reasons for a ticket number or load id.",
                    "parameters": {
                        "type": "object",
                        "properties": {"ticket_number": {"type": "string"}, "load_id": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "load_facts",
                    "description": "Get broker, invoice, status, assignment, and miles for one load.",
                    "parameters": {
                        "type": "object",
                        "properties": {"load_id": {"type": "string"}},
                        "required": ["load_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "driver_activity",
                    "description": "Get load and mileage activity for a named driver.",
                    "parameters": {
                        "type": "object",
                        "properties": {"driver_name": {"type": "string"}},
                        "required": ["driver_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_driver",
                    "description": "Add a new driver to the fleet.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "truck_id": {"type": "string"},
                            "trailer_id": {"type": "string"},
                            "home_region": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "remove_driver",
                    "description": "Remove driver by name or driver id when no active loads are assigned.",
                    "parameters": {
                        "type": "object",
                        "properties": {"driver_ref": {"type": "string"}},
                        "required": ["driver_ref"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "export_billing_ready",
                    "description": "Export billing artifacts for one load or all billing-ready loads.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "load_id": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "dispatch_send",
                    "description": "Send one assigned load dispatch packet to driver app.",
                    "parameters": {
                        "type": "object",
                        "properties": {"load_id": {"type": "string"}},
                        "required": ["load_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "dispatch_send_batch",
                    "description": "Send dispatch packets in batch for assigned loads.",
                    "parameters": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 40}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_ticket_sla_policy",
                    "description": "Set auto-resolve SLA policy for flagged tickets by weekday/time.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "weekday": {"type": "string"},
                            "cutoff_hhmm": {"type": "string"},
                            "enabled": {"type": "boolean"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_ticket_sla",
                    "description": "Run ticket SLA policy now and auto-resolve overdue exceptions.",
                    "parameters": {
                        "type": "object",
                        "properties": {"force": {"type": "boolean"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_missing_docs_reminders",
                    "description": "Send missing-document reminders for exception tickets.",
                    "parameters": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "billing_export_and_email",
                    "description": "Export billing-ready loads and email accounting summary.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "period": {"type": "string"},
                            "recipients": {"type": "array", "items": {"type": "string"}},
                            "driver_ids": {"type": "array", "items": {"type": "string"}},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 60},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ops_digest",
                    "description": "Return daily back-office digest: dispatch, exceptions, billing, and reminders.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_docs",
                    "description": "Run grounded document QA with citations when operational tools are insufficient.",
                    "parameters": {
                        "type": "object",
                        "properties": {"question": {"type": "string"}},
                        "required": ["question"],
                    },
                },
            },
        ]

    async def _tool_dispatch_summary(self, tenant_id: str) -> Dict[str, Any]:
        board = self.ops_engine.dispatch_board(tenant_id)
        loads = board.get("loads", [])
        drivers = board.get("drivers", [])
        available = [d for d in drivers if str(d.get("status") or "").lower() == "available"]
        assigned = [d for d in drivers if str(d.get("status") or "").lower() == "assigned"]
        return {
            "counts_by_status": board.get("counts_by_status", {}),
            "active_loads": [r.get("load_id") for r in loads if str(r.get("status") or "").lower() != "delivered"][:15],
            "drivers_total": len(drivers),
            "drivers_available": [
                {"name": d.get("name"), "truck_id": d.get("truck_id"), "driver_id": d.get("driver_id")} for d in available[:12]
            ],
            "drivers_assigned": len(assigned),
        }

    async def _tool_assign_available_drivers(self, tenant_id: str, limit: int, actor: str) -> Dict[str, Any]:
        prefinalized = self.ops_engine._finalize_approved_assigned_loads(tenant_id, actor=actor)
        recycled = self.ops_engine._release_drivers_from_completed_reviews(tenant_id, actor=actor)
        assigned_count, errors, assignments = self.ops_engine._auto_assign_planned_loads(
            tenant_id=tenant_id,
            actor=actor,
            limit=max(1, min(int(limit or 1), 40)),
        )
        quick_reviews: List[Dict[str, Any]] = []
        for row in assignments:
            reviewed = self.ops_engine._run_quick_ticket_review(tenant_id, str(row.get("load_id") or ""), actor=actor)
            if reviewed:
                quick_reviews.append(reviewed)
        board = self.ops_engine.dispatch_board(tenant_id)
        return {
            "assigned_count": assigned_count,
            "assignments": assignments,
            "ticket_checks": quick_reviews,
            "finalized_from_prior_approved": prefinalized,
            "released_drivers": recycled,
            "errors": errors,
            "counts_by_status": board.get("counts_by_status", {}),
        }

    async def _tool_assign_specific_load(self, tenant_id: str, load_id: str, actor: str) -> Dict[str, Any]:
        normalized = self._normalize_load_id(load_id)
        assignment = self.ops_engine.assign_load(
            LoadAssignmentRequest(load_id=normalized, auto=True),
            tenant_id=tenant_id,
            actor=actor,
        )
        return {"load_id": normalized, "assignment": assignment}

    async def _tool_create_load(self, tenant_id: str, args: Dict[str, Any], actor: str) -> Dict[str, Any]:
        payload = LoadCreateRequest(
            customer=str(args.get("customer") or "DEMO CUSTOMER"),
            broker=str(args.get("broker") or "").strip() or None,
            pickup_location=str(args.get("pickup_location") or "Unknown pickup"),
            delivery_location=str(args.get("delivery_location") or "Unknown delivery"),
            planned_miles=float(args.get("planned_miles") or 0.0),
            rate_total=float(args.get("rate_total") or 0.0),
            zone=str(args.get("zone") or "").strip() or None,
            source="atlas_free_roam",
        )
        row = self.ops_engine.create_load(payload, tenant_id=tenant_id, actor=actor)
        return {"created": True, "load": row}

    async def _tool_set_load_status(self, tenant_id: str, load_id: str, status: str, actor: str) -> Dict[str, Any]:
        normalized = self._normalize_load_id(load_id)
        desired = str(status or "").strip().lower()
        try:
            status_enum = LoadStatus(desired)
        except Exception as exc:
            raise ValueError(f"Unsupported status '{status}'.") from exc
        row = self.ops_engine.transition_load_status(
            load_id=normalized,
            request=LoadStatusTransitionRequest(status=status_enum),
            tenant_id=tenant_id,
            actor=actor,
        )
        return {"updated": True, "load": row}

    async def _tool_review_ticket(self, tenant_id: str, load_id: str, actor: str) -> Dict[str, Any]:
        normalized = self._normalize_load_id(load_id)
        load = ops_state_store.get_load(tenant_id, normalized) or {}
        reviewed = await self.ops_engine.review_ticket(
            TicketReviewRequest(
                load_id=normalized,
                ticket_number=f"TKT-AUTO-{int(time.time())}",
                rated_miles=float(load.get("planned_miles") or 0.0),
                gps_miles=round(float(load.get("planned_miles") or 0.0) * 1.01, 2),
                zone=load.get("zone"),
                expected_rate=float(load.get("rate_total") or 0.0),
            ),
            tenant_id=tenant_id,
            actor=actor,
        )
        return reviewed.model_dump(mode="json")

    async def _tool_resolve_ticket(
        self,
        tenant_id: str,
        ticket_number: Optional[str],
        review_id: Optional[str],
        note: str,
        actor: str,
    ) -> Dict[str, Any]:
        selected = None
        token = self._normalize_ticket(ticket_number)
        if review_id:
            selected = ops_state_store.get_review(tenant_id, review_id)
        if not selected and token:
            for row in ops_state_store.list_reviews(tenant_id):
                value = self._normalize_ticket(row.get("ticket_number"))
                if value and (value == token or token in value):
                    selected = row
                    break
        if not selected:
            raise ValueError("Ticket not found for resolve action.")

        decision = self.ops_engine.apply_ticket_decision(
            review_id=str(selected.get("review_id")),
            request=TicketDecisionRequest(decision="resolve", note=note or "Resolved by Atlas free-roam agent"),
            tenant_id=tenant_id,
            actor=actor,
        )
        return {"resolved": True, "decision": decision, "ticket_number": selected.get("ticket_number")}

    async def _tool_resolve_flagged_tickets(self, tenant_id: str, limit: int, actor: str) -> Dict[str, Any]:
        max_items = max(1, min(int(limit or 1), 10))
        flagged = [
            row for row in ops_state_store.list_reviews(tenant_id)
            if str(row.get("status") or "").lower() == "exception"
        ][:max_items]
        resolved = []
        errors = []
        for row in flagged:
            review_id = str(row.get("review_id") or "")
            if not review_id:
                continue
            try:
                decision = self.ops_engine.apply_ticket_decision(
                    review_id=review_id,
                    request=TicketDecisionRequest(
                        decision="resolve",
                        note="Resolved by Atlas free-roam agent from flagged queue",
                    ),
                    tenant_id=tenant_id,
                    actor=actor,
                )
                resolved.append(
                    {
                        "review_id": review_id,
                        "ticket_number": row.get("ticket_number"),
                        "load_id": row.get("load_id"),
                        "decision": decision,
                    }
                )
            except Exception as exc:
                errors.append(f"{review_id}: {exc}")
        return {"resolved_count": len(resolved), "resolved": resolved, "errors": errors}

    async def _tool_ticket_status(
        self,
        tenant_id: str,
        ticket_number: Optional[str],
        load_id: Optional[str],
    ) -> Dict[str, Any]:
        token = self._normalize_ticket(ticket_number)
        normalized_load = self._normalize_load_id(load_id) if load_id else ""
        for row in ops_state_store.list_reviews(tenant_id):
            review_ticket = self._normalize_ticket(row.get("ticket_number"))
            review_load = str(row.get("load_id") or "").upper()
            if token and review_ticket and (review_ticket == token or token in review_ticket):
                return row
            if normalized_load and review_load == normalized_load:
                return row
        raise ValueError("No matching reviewed ticket found.")

    async def _tool_load_facts(self, tenant_id: str, load_id: str) -> Dict[str, Any]:
        normalized = self._normalize_load_id(load_id)
        load = ops_state_store.get_load(tenant_id, normalized)
        if not load:
            raise ValueError(f"Load {normalized} not found.")

        docs = document_registry.find_related(normalized, tenant_id=tenant_id)
        invoice_id = None
        invoice_amount = None
        broker_name = load.get("broker")
        for doc in docs:
            doc_type = str(doc.get("document_type") or "")
            extracted = doc.get("extracted_data") or {}
            if doc_type == DocumentType.INVOICE.value and not invoice_id:
                invoice_id = extracted.get("invoice_number")
                invoice_amount = extracted.get("total_amount")
            if not broker_name and doc_type in {DocumentType.RATE_CONFIRMATION.value, DocumentType.INVOICE.value}:
                broker_name = extracted.get("broker_name")

        return {
            "load_id": normalized,
            "status": load.get("status"),
            "customer": load.get("customer"),
            "broker": broker_name or "unknown broker",
            "invoice_number": invoice_id or "unknown invoice",
            "invoice_amount": invoice_amount,
            "planned_miles": load.get("planned_miles"),
            "assignment": load.get("assignment") or {},
        }

    async def _tool_driver_activity(self, tenant_id: str, driver_name: str) -> Dict[str, Any]:
        board = self.ops_engine.dispatch_board(tenant_id)
        drivers = board.get("drivers", [])
        loads = board.get("loads", [])
        target = None
        query = str(driver_name or "").strip().lower()
        for row in drivers:
            name = str(row.get("name") or "").strip().lower()
            if name == query or query in name:
                target = row
                break
        if not target:
            raise ValueError(f"Driver '{driver_name}' not found.")

        target_name = str(target.get("name") or "")
        related = []
        for row in loads:
            assignment = row.get("assignment") or {}
            assigned_name = str(assignment.get("driver_name") or "")
            if assigned_name and assigned_name.lower() == target_name.lower():
                related.append(row)
        completed = [r for r in related if str(r.get("status") or "").lower() == "delivered"]
        return {
            "driver": target.get("name"),
            "truck_id": target.get("truck_id"),
            "status": target.get("status"),
            "total_loads": len(related),
            "completed_loads": [r.get("load_id") for r in completed],
            "completed_miles": round(sum(float(r.get("planned_miles") or 0.0) for r in completed), 1),
            "total_miles": round(sum(float(r.get("planned_miles") or 0.0) for r in related), 1),
        }

    async def _tool_create_driver(self, tenant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return ops_state_store.create_driver(
            tenant_id,
            name=str(args.get("name") or ""),
            truck_id=str(args.get("truck_id") or "").strip() or None,
            trailer_id=str(args.get("trailer_id") or "").strip() or None,
            home_region=str(args.get("home_region") or "FL-West"),
        )

    async def _tool_remove_driver(self, tenant_id: str, driver_ref: str) -> Dict[str, Any]:
        return ops_state_store.remove_driver(tenant_id, driver_ref=str(driver_ref or ""))

    async def _tool_export_billing_ready(self, tenant_id: str, load_id: Optional[str], limit: int, actor: str) -> Dict[str, Any]:
        max_items = max(1, min(int(limit or 10), 30))
        rows = self.ops_engine.billing_readiness(tenant_id)
        ready = [row for row in rows if bool(row.billing_ready)]
        if load_id:
            target = self._normalize_load_id(load_id)
            ready = [row for row in ready if str(row.load_id).upper() == target]
        exports = []
        errors = []
        for row in ready[:max_items]:
            try:
                exports.append(self.ops_engine.create_mcleod_export(str(row.load_id), tenant_id=tenant_id, actor=actor))
            except Exception as exc:
                errors.append(f"{row.load_id}: {exc}")
        return {"exported": len(exports), "exports": exports, "errors": errors}

    @staticmethod
    def _weekday_index(name: str) -> int:
        labels = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        return labels.get(str(name or "").strip().lower(), 5)

    @staticmethod
    def _is_in_period(load_row: Dict[str, Any], period: str) -> bool:
        text = str(period or "").strip().lower()
        if not text or text in {"all", "any"}:
            return True
        now = datetime.now(timezone.utc)
        ts = str(load_row.get("delivery_time") or load_row.get("pickup_time") or load_row.get("updated_at") or "")
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
        except Exception:
            return text in {"all", "this_week"}

        if text in {"today", "daily"}:
            return dt.date() == now.date()
        if text in {"this_week", "week", "weekly"}:
            return dt.isocalendar().week == now.isocalendar().week and dt.year == now.year
        if text in {"last_7d", "last7d", "7d"}:
            return dt >= now - timedelta(days=7)
        return True

    async def _tool_dispatch_send(self, tenant_id: str, load_id: str, actor: str) -> Dict[str, Any]:
        normalized = self._normalize_load_id(load_id)
        load = ops_state_store.get_load(tenant_id, normalized)
        if not load:
            raise ValueError(f"Load {normalized} not found.")
        status = str(load.get("status") or "").lower()
        if status == LoadStatus.PLANNED.value:
            self.ops_engine.assign_load(LoadAssignmentRequest(load_id=normalized, auto=True), tenant_id=tenant_id, actor=actor)
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

    async def _tool_dispatch_send_batch(self, tenant_id: str, limit: int, actor: str) -> Dict[str, Any]:
        max_items = max(1, min(int(limit or 10), 40))
        board = self.ops_engine.dispatch_board(tenant_id)
        loads = board.get("loads", [])
        candidates = [row for row in loads if str(row.get("status") or "").lower() in {"assigned", "en_route", "planned"}][:max_items]
        sent = []
        errors = []
        for row in candidates:
            try:
                sent.append(await self._tool_dispatch_send(tenant_id, str(row.get("load_id") or ""), actor))
            except Exception as exc:
                errors.append(f"{row.get('load_id')}: {exc}")
        return {"sent": len(sent), "receipts": sent, "errors": errors}

    async def _tool_set_ticket_sla_policy(self, tenant_id: str, weekday: str, cutoff_hhmm: str, enabled: bool) -> Dict[str, Any]:
        policy = ops_state_store.upsert_automation_policy(
            tenant_id,
            "ticket_sla",
            {
                "status": "active" if enabled else "disabled",
                "weekday": (weekday or "saturday").strip().lower(),
                "cutoff_hhmm": (cutoff_hhmm or "23:50").strip(),
                "auto_resolve": bool(enabled),
                "note": "Auto-resolve flagged tickets at SLA cutoff for billing continuity.",
            },
        )
        return policy

    async def _tool_run_ticket_sla(self, tenant_id: str, actor: str, force: bool = False) -> Dict[str, Any]:
        policy = ops_state_store.get_automation_policy(tenant_id, "ticket_sla")
        if not policy:
            policy = await self._tool_set_ticket_sla_policy(tenant_id, "saturday", "23:50", True)
        if str(policy.get("status") or "") != "active":
            return {"executed": False, "reason": "policy_disabled", "resolved": 0}

        now = datetime.now(timezone.utc)
        due = bool(force)
        if not due:
            weekday = self._weekday_index(str(policy.get("weekday") or "saturday"))
            hhmm = str(policy.get("cutoff_hhmm") or "23:50")
            parts = hhmm.split(":")
            try:
                cutoff = dt_time(hour=int(parts[0]), minute=int(parts[1]))
            except Exception:
                cutoff = dt_time(hour=23, minute=50)
            due = now.weekday() == weekday and now.time() >= cutoff

        if not due:
            return {"executed": False, "reason": "not_due", "resolved": 0, "policy": policy}

        result = await self._tool_resolve_flagged_tickets(tenant_id, 20, actor)
        return {"executed": True, "policy": policy, **result}

    async def _tool_send_missing_docs_reminders(self, tenant_id: str, limit: int, actor: str) -> Dict[str, Any]:
        max_items = max(1, min(int(limit or 20), 50))
        flagged = [
            row for row in ops_state_store.list_reviews(tenant_id)
            if str(row.get("status") or "").lower() == "exception" and (row.get("missing_documents") or [])
        ][:max_items]
        reminders = []
        for row in flagged:
            load_id = str(row.get("load_id") or "")
            load = ops_state_store.get_load(tenant_id, load_id) or {}
            assignment = load.get("assignment") or {}
            driver_name = str(assignment.get("driver_name") or assignment.get("driver_id") or "driver")
            broker = str(load.get("broker") or "broker")
            missing = ", ".join(row.get("missing_documents") or [])
            note = (
                f"Reminder: ticket {row.get('ticket_number')} for {load_id} is missing [{missing}]. "
                f"Please upload before billing cutoff."
            )
            msg_driver = ops_state_store.add_outbound_message(
                tenant_id,
                channel="driver_reminder",
                recipient=driver_name,
                payload={"load_id": load_id, "note": note, "ticket_number": row.get("ticket_number")},
                status="queued",
            )
            msg_broker = ops_state_store.add_outbound_message(
                tenant_id,
                channel="broker_reminder",
                recipient=broker,
                payload={"load_id": load_id, "note": note, "ticket_number": row.get("ticket_number")},
                status="queued",
            )
            reminders.append({"load_id": load_id, "driver_msg": msg_driver, "broker_msg": msg_broker})
            ops_state_store.record_timeline_event(
                tenant_id,
                load_id,
                event_type="missing_docs_reminder_queued",
                actor=actor,
                details={"ticket_number": row.get("ticket_number"), "missing_documents": row.get("missing_documents")},
            )
        return {"queued": len(reminders), "items": reminders}

    async def _tool_billing_export_and_email(
        self,
        tenant_id: str,
        actor: str,
        period: str,
        recipients: List[str],
        driver_ids: List[str],
        limit: int,
    ) -> Dict[str, Any]:
        max_items = max(1, min(int(limit or 30), 60))
        rows = self.ops_engine.billing_readiness(tenant_id)
        ready = [row for row in rows if bool(row.billing_ready)]
        driver_filter = {str(item or "").strip().upper() for item in (driver_ids or []) if str(item or "").strip()}
        selected = []
        for row in ready:
            load = ops_state_store.get_load(tenant_id, str(row.load_id)) or {}
            if not self._is_in_period(load, period):
                continue
            if driver_filter:
                assignment = load.get("assignment") or {}
                driver_id = str(assignment.get("driver_id") or "").upper()
                if driver_id not in driver_filter:
                    continue
            selected.append(row)
            if len(selected) >= max_items:
                break

        exports = []
        errors = []
        for row in selected:
            try:
                exports.append(self.ops_engine.create_mcleod_export(str(row.load_id), tenant_id=tenant_id, actor=actor))
            except Exception as exc:
                errors.append(f"{row.load_id}: {exc}")

        recipient_list = [str(x or "").strip() for x in (recipients or []) if str(x or "").strip()]
        if not recipient_list:
            recipient_list = ["accounting@shams.local"]
        subject = f"SHAMS Billing Export ({period or 'all'}) - {len(exports)} loads"
        body = (
            f"Exports generated: {len(exports)}\n"
            f"Period: {period or 'all'}\n"
            f"Load IDs: {', '.join(str(row.get('load_id')) for row in exports[:30])}\n"
            f"Errors: {errors[:2]}"
        )

        email_result: Dict[str, Any]
        try:
            sent = await microsoft_graph_service.send_mail(
                to_addresses=recipient_list,
                subject=subject,
                body_text=body,
            )
            email_result = {"status": "sent", **sent}
        except Exception as exc:
            queued = ops_state_store.add_outbound_message(
                tenant_id,
                channel="accounting_email",
                recipient=",".join(recipient_list),
                payload={"subject": subject, "body": body, "exports": exports},
                status="queued_local",
            )
            email_result = {"status": "queued_local", "reason": str(exc), "queue": queued}
        return {"exported": len(exports), "exports": exports, "errors": errors, "email": email_result}

    async def _tool_ops_digest(self, tenant_id: str) -> Dict[str, Any]:
        board = self.ops_engine.dispatch_board(tenant_id)
        reviews = ops_state_store.list_reviews(tenant_id)
        flagged = [row for row in reviews if str(row.get("status") or "").lower() == "exception"]
        billing = self.ops_engine.billing_readiness(tenant_id)
        ready = [row for row in billing if bool(row.billing_ready)]
        reminders = ops_state_store.list_outbound_messages(tenant_id, limit=10)
        dispatches = ops_state_store.list_dispatch_messages(tenant_id, limit=10)
        return {
            "counts_by_status": board.get("counts_by_status", {}),
            "drivers_total": len(board.get("drivers", [])),
            "drivers_available": sum(1 for d in board.get("drivers", []) if str(d.get("status") or "").lower() == "available"),
            "flagged_tickets": len(flagged),
            "billing_ready": len(ready),
            "recent_dispatches": dispatches[:5],
            "recent_outbound_messages": reminders[:5],
        }

    async def _tool_query_docs(self, tenant_id: str, question: str) -> Dict[str, Any]:
        response = await rag_engine.query(
            QueryRequest(
                query=str(question or "").strip(),
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
            extra_context=self.ops_engine._build_system_context(tenant_id),
        )
        return response.model_dump(mode="json")

    async def _execute_tool(
        self,
        name: str,
        args: Dict[str, Any],
        tenant_id: str,
        actor: str,
    ) -> Dict[str, Any]:
        if name == "dispatch_summary":
            return await self._tool_dispatch_summary(tenant_id)
        if name == "assign_available_drivers":
            return await self._tool_assign_available_drivers(tenant_id, int(args.get("limit") or 4), actor)
        if name == "assign_specific_load":
            return await self._tool_assign_specific_load(tenant_id, str(args.get("load_id") or ""), actor)
        if name == "create_load":
            return await self._tool_create_load(tenant_id, args, actor)
        if name == "set_load_status":
            return await self._tool_set_load_status(
                tenant_id,
                str(args.get("load_id") or ""),
                str(args.get("status") or ""),
                actor,
            )
        if name == "review_ticket":
            return await self._tool_review_ticket(tenant_id, str(args.get("load_id") or ""), actor)
        if name == "resolve_ticket":
            return await self._tool_resolve_ticket(
                tenant_id=tenant_id,
                ticket_number=args.get("ticket_number"),
                review_id=args.get("review_id"),
                note=str(args.get("note") or ""),
                actor=actor,
            )
        if name == "resolve_flagged_tickets":
            return await self._tool_resolve_flagged_tickets(tenant_id, int(args.get("limit") or 1), actor)
        if name == "ticket_status":
            return await self._tool_ticket_status(
                tenant_id=tenant_id,
                ticket_number=args.get("ticket_number"),
                load_id=args.get("load_id"),
            )
        if name == "load_facts":
            return await self._tool_load_facts(tenant_id, str(args.get("load_id") or ""))
        if name == "driver_activity":
            return await self._tool_driver_activity(tenant_id, str(args.get("driver_name") or ""))
        if name == "create_driver":
            return await self._tool_create_driver(tenant_id, args)
        if name == "remove_driver":
            return await self._tool_remove_driver(tenant_id, str(args.get("driver_ref") or ""))
        if name == "export_billing_ready":
            return await self._tool_export_billing_ready(
                tenant_id=tenant_id,
                load_id=args.get("load_id"),
                limit=int(args.get("limit") or 10),
                actor=actor,
            )
        if name == "dispatch_send":
            return await self._tool_dispatch_send(tenant_id, str(args.get("load_id") or ""), actor)
        if name == "dispatch_send_batch":
            return await self._tool_dispatch_send_batch(tenant_id, int(args.get("limit") or 10), actor)
        if name == "set_ticket_sla_policy":
            return await self._tool_set_ticket_sla_policy(
                tenant_id=tenant_id,
                weekday=str(args.get("weekday") or "saturday"),
                cutoff_hhmm=str(args.get("cutoff_hhmm") or "23:50"),
                enabled=bool(args.get("enabled", True)),
            )
        if name == "run_ticket_sla":
            return await self._tool_run_ticket_sla(
                tenant_id=tenant_id,
                actor=actor,
                force=bool(args.get("force", False)),
            )
        if name == "send_missing_docs_reminders":
            return await self._tool_send_missing_docs_reminders(tenant_id, int(args.get("limit") or 20), actor)
        if name == "billing_export_and_email":
            return await self._tool_billing_export_and_email(
                tenant_id=tenant_id,
                actor=actor,
                period=str(args.get("period") or "this_week"),
                recipients=list(args.get("recipients") or []),
                driver_ids=list(args.get("driver_ids") or []),
                limit=int(args.get("limit") or 30),
            )
        if name == "ops_digest":
            return await self._tool_ops_digest(tenant_id)
        if name == "query_docs":
            return await self._tool_query_docs(tenant_id, str(args.get("question") or ""))
        raise ValueError(f"Unsupported tool: {name}")

    async def query(
        self,
        query: str,
        tenant_id: str,
        actor: str,
        session_id: str = "atlas",
        load_id_hint: Optional[str] = None,
    ) -> Optional[CopilotQueryResponse]:
        if not self.is_enabled():
            return None

        started = time.time()
        user_query = str(query or "").strip()
        if not user_query:
            return None

        # Persist high-level workflow directives for this session.
        if "should always" in user_query.lower():
            self._remember(tenant_id, actor, session_id, "system", f"Directive from user: {user_query}")

        # Fast-path: explicit resolve requests should not depend on model tool-planning quality.
        q = user_query.lower()
        try:
            await self._tool_run_ticket_sla(tenant_id=tenant_id, actor=actor, force=False)
        except Exception:
            pass

        if "assign" in q and any(token in q for token in ["available", "planned", "next loads", "all"]):
            result = await self._tool_assign_available_drivers(tenant_id, limit=8, actor=actor)
            assigned = int(result.get("assigned_count") or 0)
            answer = f"Assigned {assigned} load(s) from planned queue."
            if result.get("assignments"):
                preview = ", ".join(
                    f"{row.get('load_id')} -> {row.get('driver_name') or row.get('driver_id')}"
                    for row in (result.get("assignments") or [])[:5]
                )
                answer += f" Assignments: {preview}."
            if result.get("errors"):
                answer += f" First issue: {result['errors'][0]}."
            elapsed = (time.time() - started) * 1000
            self._remember(tenant_id, actor, session_id, "user", user_query)
            self._remember(tenant_id, actor, session_id, "assistant", answer)
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.93 if assigned else 0.78,
                processing_time_ms=elapsed,
                route="free_roam",
                actions=[{"tool": "assign_available_drivers", "args": {"limit": 8}, "ok": True, "preview": str(result)[:220]}],
            )

        if "driver" in q and any(token in q for token in ["available", "who are", "roster", "how many"]) and "assign" not in q:
            summary = await self._tool_dispatch_summary(tenant_id)
            available = summary.get("drivers_available") or []
            answer = (
                f"{len(available)} drivers are available right now: "
                + ", ".join(f"{row.get('name')} ({row.get('truck_id')})" for row in available[:8])
            )
            if not available:
                answer = "No drivers are currently available."
            elapsed = (time.time() - started) * 1000
            self._remember(tenant_id, actor, session_id, "user", user_query)
            self._remember(tenant_id, actor, session_id, "assistant", answer)
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.96,
                processing_time_ms=elapsed,
                route="free_roam",
                actions=[{"tool": "dispatch_summary", "args": {}, "ok": True, "preview": str(summary)[:220]}],
            )

        if "dispatch" in q and any(token in q for token in ["driver app", "send", "push", "notify"]):
            load_ref = self._extract_load_id(user_query)
            if load_ref:
                result = await self._tool_dispatch_send(tenant_id, load_ref, actor)
                answer = f"Dispatch packet sent for {load_ref} to driver app."
                actions = [{"tool": "dispatch_send", "args": {"load_id": load_ref}, "ok": True, "preview": str(result)[:220]}]
            else:
                result = await self._tool_dispatch_send_batch(tenant_id, limit=10, actor=actor)
                answer = f"Sent {int(result.get('sent') or 0)} dispatch packet(s) to driver app."
                if result.get("errors"):
                    answer += f" First issue: {result['errors'][0]}."
                actions = [{"tool": "dispatch_send_batch", "args": {"limit": 10}, "ok": True, "preview": str(result)[:220]}]
            elapsed = (time.time() - started) * 1000
            self._remember(tenant_id, actor, session_id, "user", user_query)
            self._remember(tenant_id, actor, session_id, "assistant", answer)
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "dispatch_board", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.92,
                processing_time_ms=elapsed,
                route="free_roam",
                actions=actions,
            )

        if "flagged" in q and "ticket" in q and "resolve" not in q:
            flagged = [
                row for row in ops_state_store.list_reviews(tenant_id)
                if str(row.get("status") or "").lower() == "exception"
            ][:5]
            if not flagged:
                answer = "There are currently no flagged tickets."
            else:
                lines = [f"{row.get('ticket_number')} ({row.get('load_id')})" for row in flagged]
                answer = f"{len(flagged)} flagged ticket(s): " + " | ".join(lines)
            elapsed = (time.time() - started) * 1000
            self._remember(tenant_id, actor, session_id, "user", user_query)
            self._remember(tenant_id, actor, session_id, "assistant", answer)
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "ticket_queue", "document_type": "ticket_review", "similarity": 1.0}],
                confidence=0.94,
                processing_time_ms=elapsed,
                route="free_roam",
                actions=[{"tool": "ticket_status", "args": {"query": "flagged_queue"}, "ok": True, "preview": answer[:220]}],
            )

        load_ref = self._extract_load_id(user_query)
        if load_ref and any(token in q for token in ["broker", "invoice", "who did", "driver", "miles", "status"]):
            facts = await self._tool_load_facts(tenant_id, load_ref)
            assignment = facts.get("assignment") or {}
            answer_parts = [f"{facts.get('load_id')}: status {facts.get('status')}"]
            if "broker" in q:
                answer_parts.append(f"broker {facts.get('broker')}")
            if "invoice" in q:
                invoice = facts.get("invoice_number")
                amount = facts.get("invoice_amount")
                if amount is not None:
                    answer_parts.append(f"invoice {invoice} (${float(amount):,.2f})")
                else:
                    answer_parts.append(f"invoice {invoice}")
            if any(token in q for token in ["who did", "driver"]):
                answer_parts.append(
                    "driver "
                    + str(assignment.get("driver_name") or assignment.get("driver_id") or "unassigned")
                )
            if "miles" in q:
                answer_parts.append(f"planned miles {facts.get('planned_miles')}")
            answer = ", ".join(answer_parts)
            elapsed = (time.time() - started) * 1000
            self._remember(tenant_id, actor, session_id, "user", user_query)
            self._remember(tenant_id, actor, session_id, "assistant", answer)
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "system_state", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.95,
                processing_time_ms=elapsed,
                route="free_roam",
                actions=[{"tool": "load_facts", "args": {"load_id": load_ref}, "ok": True, "preview": str(facts)[:220]}],
            )

        if "resolve" in q and any(token in q for token in ["flagged ticket", "flagged tickets", "exceptions", "exception queue"]):
            count = 2 if "2" in q or "two" in q else 1
            result = await self._tool_resolve_flagged_tickets(tenant_id, count, actor)
            elapsed = (time.time() - started) * 1000
            resolved_count = int(result.get("resolved_count") or 0)
            answer = f"Resolved {resolved_count} flagged ticket(s)."
            if result.get("errors"):
                answer += f" First error: {result['errors'][0]}."
            self._remember(tenant_id, actor, session_id, "user", user_query)
            self._remember(tenant_id, actor, session_id, "assistant", answer)
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "ticket_queue", "document_type": "ticket_review", "similarity": 1.0}],
                confidence=0.93 if resolved_count else 0.72,
                processing_time_ms=elapsed,
                route="free_roam",
                actions=[{"tool": "resolve_flagged_tickets", "args": {"limit": count}, "ok": True, "preview": str(result)[:220]}],
            )

        if any(token in q for token in ["billing", "export"]) and any(token in q for token in ["email", "accounting", "pay period", "payroll"]):
            result = await self._tool_billing_export_and_email(
                tenant_id=tenant_id,
                actor=actor,
                period="this_week",
                recipients=["accounting@shams.local"],
                driver_ids=[],
                limit=30,
            )
            exported = int(result.get("exported") or 0)
            email_status = (result.get("email") or {}).get("status") or "queued_local"
            answer = f"Exported {exported} billing artifact(s) and {email_status} accounting notification."
            elapsed = (time.time() - started) * 1000
            self._remember(tenant_id, actor, session_id, "user", user_query)
            self._remember(tenant_id, actor, session_id, "assistant", answer)
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "billing_readiness", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.9,
                processing_time_ms=elapsed,
                route="free_roam",
                actions=[{"tool": "billing_export_and_email", "args": {"period": "this_week"}, "ok": True, "preview": str(result)[:220]}],
            )

        if any(token in q for token in ["digest", "summary", "back office", "back office workers"]):
            digest = await self._tool_ops_digest(tenant_id)
            answer = (
                f"Digest: statuses {digest.get('counts_by_status')}, "
                f"drivers available {digest.get('drivers_available')}/{digest.get('drivers_total')}, "
                f"flagged tickets {digest.get('flagged_tickets')}, billing ready {digest.get('billing_ready')}."
            )
            elapsed = (time.time() - started) * 1000
            self._remember(tenant_id, actor, session_id, "user", user_query)
            self._remember(tenant_id, actor, session_id, "assistant", answer)
            return CopilotQueryResponse(
                answer=answer,
                sources=[{"filename": "ops_digest", "document_type": "system_state", "similarity": 1.0}],
                confidence=0.9,
                processing_time_ms=elapsed,
                route="free_roam",
                actions=[{"tool": "ops_digest", "args": {}, "ok": True, "preview": str(digest)[:220]}],
            )

        working_query = user_query
        if load_id_hint and "load" not in user_query.lower():
            working_query = f"{user_query}\n\nLoad hint: {load_id_hint}"

        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are Atlas, SHAMS's free-roam operations agent. "
                    "Use tools to perform real actions in the dispatch system when asked. "
                    "Never pretend to execute an action. If a tool fails, explain exactly why. "
                    "You can create/update loads, assign drivers, review/resolve tickets, manage drivers, and export billing. "
                    "Keep answers concise and operational."
                ),
            }
        ]
        messages.extend(self._history(tenant_id, actor, session_id))
        messages.append({"role": "user", "content": working_query})

        actions: List[Dict[str, Any]] = []
        tools = self._tool_schemas()
        max_steps = max(1, min(int(self.settings.free_roam_max_steps), 12))

        try:
            for _ in range(max_steps):
                completion = await self._client.chat.completions.create(
                    model=self.settings.openrouter_model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.1,
                    max_tokens=380,
                    extra_headers={"X-Title": "SHAMS Ops"},
                )
                message = completion.choices[0].message
                tool_calls = message.tool_calls or []

                if tool_calls:
                    assistant_payload = message.model_dump(mode="json", exclude_none=True)
                    messages.append(assistant_payload)
                    for call in tool_calls:
                        name = str(call.function.name or "")
                        raw_args = str(call.function.arguments or "{}")
                        try:
                            parsed_args = json.loads(raw_args)
                        except Exception:
                            parsed_args = {}
                        try:
                            result = await self._execute_tool(name, parsed_args, tenant_id=tenant_id, actor=actor)
                            tool_result = {"ok": True, "result": result}
                        except Exception as exc:
                            tool_result = {"ok": False, "error": str(exc)}
                        actions.append(
                            {
                                "tool": name,
                                "args": parsed_args,
                                "ok": bool(tool_result.get("ok")),
                                "preview": (tool_result.get("error") or str(tool_result.get("result"))[:220]),
                            }
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": name,
                                "content": json.dumps(tool_result, ensure_ascii=True),
                            }
                        )
                    continue

                answer = str(message.content or "").strip()
                if not answer:
                    answer = "Done. I executed available actions. Ask for a dispatch/ticket/billing check for details."
                elapsed = (time.time() - started) * 1000
                sources = [{"filename": "agent_actions", "document_type": "system_state", "similarity": 1.0}]
                confidence = 0.91 if actions else 0.82
                lowered = answer.lower()
                if "couldn't" in lowered or "not found" in lowered:
                    confidence = min(confidence, 0.74)
                self._remember(tenant_id, actor, session_id, "user", working_query)
                self._remember(tenant_id, actor, session_id, "assistant", answer)
                return CopilotQueryResponse(
                    answer=answer,
                    sources=sources,
                    confidence=confidence,
                    processing_time_ms=elapsed,
                    route="free_roam",
                    actions=actions,
                )
        except Exception as exc:
            logger.error("Free-roam agent execution failed", error=str(exc), tenant_id=tenant_id)
            return None

        elapsed = (time.time() - started) * 1000
        return CopilotQueryResponse(
            answer="I hit the action-step limit. Ask me to continue from current state.",
            sources=[{"filename": "agent_actions", "document_type": "system_state", "similarity": 1.0}],
            confidence=0.7,
            processing_time_ms=elapsed,
            route="free_roam",
            actions=actions,
        )
