"""Agent OS orchestration service layered on top of SHAMS ops tools."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.models.agent_os import (
    AgentActionType,
    AgentApprovalDecisionRequest,
    AgentApprovalRecord,
    AgentAutonomyLevel,
    AgentExecutionMode,
    AgentPolicyPatchRequest,
    AgentPolicyRule,
    AgentRunRecord,
    AgentRunRequest,
    AgentRunResponse,
    AgentRunStatus,
    AgentStepRecord,
    AgentStepStatus,
    AgentRunMetrics,
    PolicyDecision,
)
from app.models.ops import LoadAssignmentRequest, TicketReviewRequest
from app.services.agent_os_state import agent_os_state_store
from app.services.ops_engine import ops_engine
from app.services.ops_state import ops_state_store


class AgentOSService:
    """Coordinator for high-autonomy Agent OS runs."""

    LOAD_ID_PATTERN = re.compile(r"\bLOAD[-_ ]?(\d{3,}[A-Z0-9]*)\b", re.IGNORECASE)
    DRIVER_NAME_PATTERN = re.compile(r"\bnamed\s+([a-z][a-z'\-]*(?:\s+[a-z][a-z'\-]*){0,3})\b", re.IGNORECASE)
    DRIVER_ADD_INTENT_PATTERN = re.compile(
        r"\b(?:add|hire|onboard)\s+(?:a\s+|new\s+)?driver\b|\bnew\s+driver\b",
        re.IGNORECASE,
    )
    DRIVER_REMOVE_INTENT_PATTERN = re.compile(
        r"\b(?:remove|delete|fire|offboard)\s+(?:the\s+)?driver\b",
        re.IGNORECASE,
    )
    DRIVER_REMOVE_NAME_PATTERN = re.compile(
        r"\b(?:remove|delete)\s+(?:the\s+)?driver(?:\s+named)?\s+([a-z][a-z'\-]*(?:\s+[a-z][a-z'\-]*){0,3})\b",
        re.IGNORECASE,
    )
    DRIVER_ID_PATTERN = re.compile(r"\bDRV[-_ ]?(\d{2,6})\b", re.IGNORECASE)
    DRIVER_TRUCK_PATTERN = re.compile(r"\b(?:truck|unit)\s*#?\s*([A-Z]\d{2,5})\b", re.IGNORECASE)
    DRIVER_TRAILER_PATTERN = re.compile(r"\btrailer\s*#?\s*(\d{3,6})\b", re.IGNORECASE)

    def _normalize_load_id(self, candidate: str) -> str:
        cleaned = str(candidate or "").strip().upper().replace("-", "").replace("_", "")
        if not cleaned.startswith("LOAD"):
            cleaned = f"LOAD{cleaned}"
        suffix = cleaned[4:]
        match = re.fullmatch(r"0*(\d+)([A-Z0-9]*)", suffix)
        if not match:
            return cleaned
        digits = str(int(match.group(1))).zfill(5)
        tail = match.group(2)
        return f"LOAD{digits}{tail}"

    def _extract_load_ids(self, text: str) -> List[str]:
        rows = []
        for match in self.LOAD_ID_PATTERN.finditer(text or ""):
            rows.append(self._normalize_load_id(f"LOAD{match.group(1)}"))
        return list(dict.fromkeys(rows))

    def _default_plan(self) -> List[AgentActionType]:
        return [
            AgentActionType.DISPATCH_ASSIGN,
            AgentActionType.TICKET_REVIEW,
            AgentActionType.BILLING_EXPORT,
        ]

    def _build_plan(self, objective: str, max_steps: int) -> List[AgentActionType]:
        q = (objective or "").strip()
        plan: List[AgentActionType] = []

        q_lower = q.lower()
        if any(token in q_lower for token in ["wipe", "reset all", "delete all", "clear all"]):
            plan.append(AgentActionType.SYSTEM_RESET)
        if self.DRIVER_ADD_INTENT_PATTERN.search(q):
            plan.append(AgentActionType.DRIVER_ADD)
        if self.DRIVER_REMOVE_INTENT_PATTERN.search(q):
            plan.append(AgentActionType.DRIVER_REMOVE)
        if any(token in q_lower for token in ["assign", "dispatch", "schedule"]):
            plan.append(AgentActionType.DISPATCH_ASSIGN)
        if any(token in q_lower for token in ["ticket", "audit", "review", "tkt"]):
            plan.append(AgentActionType.TICKET_REVIEW)
        if any(token in q_lower for token in ["billing", "invoice", "export", "mcleod"]):
            plan.append(AgentActionType.BILLING_EXPORT)

        if not plan:
            plan = self._default_plan()

        deduped: List[AgentActionType] = []
        for action in plan:
            if action not in deduped:
                deduped.append(action)
        return deduped[: max(1, max_steps)]

    @staticmethod
    def _title_name(value: str) -> str:
        return " ".join(part[:1].upper() + part[1:].lower() for part in value.strip().split() if part)

    def _extract_driver_request(self, objective: str) -> Dict[str, Optional[str]]:
        text = str(objective or "").strip()
        name = ""
        match = self.DRIVER_NAME_PATTERN.search(text)
        if match:
            name = self._title_name(match.group(1))
        if not name:
            fallback = re.search(
                r"\b(?:add|hire|onboard)\s+(?:a\s+|new\s+)?driver(?:\s+to\s+\w+)?\s+([a-z][a-z'\-]*(?:\s+[a-z][a-z'\-]*){0,3})\b",
                text,
                re.IGNORECASE,
            )
            if fallback:
                name = self._title_name(fallback.group(1))
        truck_match = self.DRIVER_TRUCK_PATTERN.search(text)
        trailer_match = self.DRIVER_TRAILER_PATTERN.search(text)
        return {
            "name": name or None,
            "truck_id": truck_match.group(1).upper() if truck_match else None,
            "trailer_id": trailer_match.group(1) if trailer_match else None,
        }

    def _extract_driver_remove_request(self, objective: str) -> Dict[str, Optional[str]]:
        text = str(objective or "").strip()
        by_name = None
        by_id = None

        remove_match = self.DRIVER_REMOVE_NAME_PATTERN.search(text)
        if remove_match:
            by_name = self._title_name(remove_match.group(1))

        id_match = self.DRIVER_ID_PATTERN.search(text)
        if id_match:
            by_id = f"DRV-{int(id_match.group(1)):03d}"

        return {"driver_name": by_name, "driver_id": by_id}

    def _snapshot(self, tenant_id: str) -> Dict[str, Any]:
        board = ops_engine.dispatch_board(tenant_id)
        loads = board.get("loads", [])
        drivers = board.get("drivers", [])
        counts: Dict[str, int] = {}
        for row in loads:
            status = str(row.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        avail = sum(1 for row in drivers if str(row.get("status") or "").lower() == "available")
        return {
            "loads_total": len(loads),
            "drivers_total": len(drivers),
            "drivers_available": avail,
            "counts_by_status": counts,
            "metrics": ops_engine.metrics(tenant_id).model_dump(mode="json"),
        }

    def _policy_for_action(self, action_type: AgentActionType) -> AgentPolicyRule:
        row = agent_os_state_store.get_policy_for_action(action_type.value)
        if not row:
            raise ValueError(f"Missing policy for action {action_type.value}")
        return AgentPolicyRule(**row)

    def _evaluate_policy(self, action_type: AgentActionType) -> PolicyDecision:
        policy = self._policy_for_action(action_type)
        if not policy.enabled:
            return PolicyDecision(
                allowed=False,
                requires_approval=False,
                reason=f"Policy '{policy.policy_id}' disabled action '{action_type.value}'",
                policy_id=policy.policy_id,
            )
        return PolicyDecision(
            allowed=True,
            requires_approval=bool(policy.requires_admin_approval),
            reason="Policy check passed",
            policy_id=policy.policy_id,
        )

    def _upsert_run(self, run: AgentRunRecord) -> AgentRunRecord:
        run.updated_at = datetime.now(timezone.utc)
        payload = run.model_dump(mode="json")
        agent_os_state_store.upsert_run(run.run_id, run.tenant_id, run.status.value, payload)
        return run

    def _upsert_step(self, step: AgentStepRecord) -> AgentStepRecord:
        step.updated_at = datetime.now(timezone.utc)
        payload = step.model_dump(mode="json")
        agent_os_state_store.upsert_step(step.step_id, step.run_id, step.step_index, step.status.value, payload)
        return step

    def _upsert_approval(self, approval: AgentApprovalRecord) -> AgentApprovalRecord:
        payload = approval.model_dump(mode="json")
        agent_os_state_store.upsert_approval(
            approval.approval_id,
            approval.run_id,
            approval.step_id,
            approval.status,
            payload,
        )
        return approval

    def _render_response(self, run_id: str) -> AgentRunResponse:
        run_row = agent_os_state_store.get_run(run_id)
        if not run_row:
            raise KeyError(run_id)
        run = AgentRunRecord(**run_row)
        steps = [AgentStepRecord(**row) for row in agent_os_state_store.list_steps(run_id)]
        approvals = [AgentApprovalRecord(**row) for row in agent_os_state_store.list_approvals(run_id)]
        return AgentRunResponse(run=run, steps=steps, approvals=approvals)

    async def _execute_action(
        self,
        action_type: AgentActionType,
        tenant_id: str,
        actor: str,
        objective: str,
        max_targets: int,
    ) -> tuple[Dict[str, Any], float]:
        load_scope = self._extract_load_ids(objective)
        if action_type == AgentActionType.DRIVER_ADD:
            driver_request = self._extract_driver_request(objective)
            driver_name = str(driver_request.get("name") or "").strip()
            if not driver_name:
                raise ValueError("Add-driver objective must include a name (example: named Ale Eddie).")

            created = ops_state_store.create_driver(
                tenant_id,
                name=driver_name,
                truck_id=driver_request.get("truck_id"),
                trailer_id=driver_request.get("trailer_id"),
            )
            driver_row = created.get("driver") or {}
            if driver_row:
                ops_state_store.record_timeline_event(
                    tenant_id=tenant_id,
                    load_id=str(driver_row.get("driver_id") or "SYSTEM"),
                    event_type="driver_added",
                    actor=actor,
                    details={
                        "created": bool(created.get("created")),
                        "driver_id": driver_row.get("driver_id"),
                        "name": driver_row.get("name"),
                        "truck_id": driver_row.get("truck_id"),
                    },
                )
            output = {
                "created": bool(created.get("created")),
                "driver": driver_row,
                "reason": created.get("reason"),
            }
            confidence = 1.0 if bool(created.get("created")) else 0.98
            return output, confidence

        if action_type == AgentActionType.DRIVER_REMOVE:
            remove_request = self._extract_driver_remove_request(objective)
            target_ref = str(remove_request.get("driver_id") or remove_request.get("driver_name") or "").strip()
            if not target_ref:
                raise ValueError("Remove-driver objective must include driver name or ID (example: remove driver Ale Eddie).")

            removed = ops_state_store.remove_driver(tenant_id, driver_ref=target_ref)
            driver_row = removed.get("driver") or {}
            if driver_row:
                ops_state_store.record_timeline_event(
                    tenant_id=tenant_id,
                    load_id=str(driver_row.get("driver_id") or "SYSTEM"),
                    event_type="driver_removed",
                    actor=actor,
                    details={
                        "removed": bool(removed.get("removed")),
                        "driver_id": driver_row.get("driver_id"),
                        "name": driver_row.get("name"),
                        "reason": removed.get("reason"),
                    },
                )
            output = {
                "removed": bool(removed.get("removed")),
                "driver": driver_row,
                "reason": removed.get("reason"),
            }
            confidence = 1.0 if bool(removed.get("removed")) else 0.9
            return output, confidence

        if action_type == AgentActionType.DISPATCH_ASSIGN:
            board = ops_engine.dispatch_board(tenant_id)
            planned = [
                row for row in board.get("loads", [])
                if str(row.get("status", "")).lower() == "planned"
            ]
            if load_scope:
                scoped = set(load_scope)
                planned = [row for row in planned if str(row.get("load_id") or "").upper() in scoped]
            assigned: List[Dict[str, Any]] = []
            errors: List[str] = []
            for row in planned[: max_targets]:
                load_id = str(row.get("load_id") or "")
                if not load_id:
                    continue
                try:
                    result = ops_engine.assign_load(
                        LoadAssignmentRequest(load_id=load_id, auto=True),
                        tenant_id=tenant_id,
                        actor=actor,
                    )
                    assigned.append({"load_id": load_id, **result})
                except Exception as exc:
                    errors.append(f"{load_id}: {exc}")
            confidence = round(float(len(assigned)) / max(1, min(len(planned), max_targets)), 4)
            return {"assigned": assigned, "errors": errors, "candidates": len(planned)}, confidence

        if action_type == AgentActionType.TICKET_REVIEW:
            board = ops_engine.dispatch_board(tenant_id)
            loads = board.get("loads", [])
            latest_review_by_load: Dict[str, str] = {}
            for review in ops_state_store.list_reviews(tenant_id):
                load_id = str(review.get("load_id") or "")
                if load_id and load_id not in latest_review_by_load:
                    latest_review_by_load[load_id] = str(review.get("status") or "")

            candidates = []
            for row in loads:
                load_id = str(row.get("load_id") or "")
                if not load_id:
                    continue
                if load_scope and load_id.upper() not in set(load_scope):
                    continue
                status = str(row.get("status") or "").lower()
                if status not in {"assigned", "en_route"}:
                    continue
                if load_id in latest_review_by_load:
                    continue
                candidates.append(load_id)

            reviewed: List[Dict[str, Any]] = []
            errors: List[str] = []
            confidences: List[float] = []
            for load_id in candidates[: max_targets]:
                try:
                    result = await ops_engine.review_ticket(
                        TicketReviewRequest(load_id=load_id),
                        tenant_id=tenant_id,
                        actor=actor,
                    )
                    payload = result.model_dump(mode="json")
                    reviewed.append(payload)
                    confidences.append(float(payload.get("final_confidence") or 0.0))
                except Exception as exc:
                    errors.append(f"{load_id}: {exc}")
            confidence = round(sum(confidences) / max(1, len(confidences)), 4)
            return {"reviewed": reviewed, "errors": errors, "candidates": len(candidates)}, confidence

        if action_type == AgentActionType.BILLING_EXPORT:
            rows = ops_engine.billing_readiness(tenant_id)
            ready = [row for row in rows if bool(row.billing_ready)]
            if load_scope:
                scoped = set(load_scope)
                ready = [row for row in ready if str(row.load_id).upper() in scoped]
            exports: List[Dict[str, Any]] = []
            errors: List[str] = []
            for row in ready[: max_targets]:
                try:
                    exports.append(
                        ops_engine.create_mcleod_export(
                            load_id=row.load_id,
                            tenant_id=tenant_id,
                            actor=actor,
                        )
                    )
                except Exception as exc:
                    errors.append(f"{row.load_id}: {exc}")
            confidence = round(float(len(exports)) / max(1, min(len(ready), max_targets)), 4)
            return {"exports": exports, "errors": errors, "candidates": len(ready)}, confidence

        if action_type == AgentActionType.SYSTEM_RESET:
            ops_state_store.reset_tenant_operational_data(tenant_id)
            return {"reset": True, "tenant_id": tenant_id}, 1.0

        raise ValueError(f"Unsupported action type: {action_type.value}")

    async def _execute_run(self, run: AgentRunRecord) -> AgentRunRecord:
        run.status = AgentRunStatus.RUNNING
        run = self._upsert_run(run)

        summary = dict(run.summary or {})
        plan_actions = summary.get("plan_actions") or []
        if not isinstance(plan_actions, list):
            raise ValueError("Invalid run plan")
        next_index = int(summary.get("next_index") or 0)
        approved_indices = [int(x) for x in (summary.get("approved_step_indices") or [])]

        for idx in range(next_index, len(plan_actions)):
            action_type = AgentActionType(str(plan_actions[idx]))
            policy = self._evaluate_policy(action_type)
            step = AgentStepRecord(
                step_id=agent_os_state_store.next_step_id(),
                run_id=run.run_id,
                step_index=idx,
                action_type=action_type,
                status=AgentStepStatus.RUNNING,
                prompt=run.objective,
                policy_decision=policy,
                input_payload={"objective": run.objective, "execution_mode": run.execution_mode.value},
            )
            self._upsert_step(step)

            if not policy.allowed:
                step.status = AgentStepStatus.FAILED
                step.error = policy.reason
                step.compensation = {"strategy": "continue", "result": "skipped_action"}
                self._upsert_step(step)
                run.warnings.append(policy.reason)
                summary["next_index"] = idx + 1
                run.summary = summary
                self._upsert_run(run)
                continue

            policy_rule = self._policy_for_action(action_type)
            if policy.requires_approval and idx not in approved_indices:
                approval = AgentApprovalRecord(
                    approval_id=agent_os_state_store.next_approval_id(),
                    run_id=run.run_id,
                    step_id=step.step_id,
                    policy_id=policy.policy_id or policy_rule.policy_id,
                    status="pending",
                    requested_by="agent",
                    note=f"Approval required for action {action_type.value}",
                )
                self._upsert_approval(approval)
                step.status = AgentStepStatus.WAITING_APPROVAL
                step.output_payload = {"approval_id": approval.approval_id}
                self._upsert_step(step)
                run.status = AgentRunStatus.WAITING_APPROVAL
                run.blocked_approval_id = approval.approval_id
                summary["next_index"] = idx
                summary["blocked_step_id"] = step.step_id
                run.summary = summary
                self._upsert_run(run)
                return run

            started = time.perf_counter()
            before = self._snapshot(run.tenant_id)
            try:
                if run.dry_run:
                    output = {"dry_run": True, "action_type": action_type.value}
                    confidence = 1.0
                else:
                    output, confidence = await self._execute_action(
                        action_type=action_type,
                        tenant_id=run.tenant_id,
                        actor=run.actor,
                        objective=run.objective,
                        max_targets=policy_rule.max_targets,
                    )
                after = self._snapshot(run.tenant_id)
                step.status = AgentStepStatus.COMPLETED
                step.latency_ms = round((time.perf_counter() - started) * 1000, 2)
                step.confidence = float(confidence)
                step.output_payload = {"result": output, "before": before, "after": after}

                # Escalate low-confidence critical steps into approval pause.
                if step.confidence < policy_rule.min_confidence:
                    approval = AgentApprovalRecord(
                        approval_id=agent_os_state_store.next_approval_id(),
                        run_id=run.run_id,
                        step_id=step.step_id,
                        policy_id=policy_rule.policy_id,
                        status="pending",
                        requested_by="agent",
                        note=(
                            f"Low confidence {step.confidence:.3f} below threshold "
                            f"{policy_rule.min_confidence:.3f} for {action_type.value}"
                        ),
                    )
                    self._upsert_approval(approval)
                    step.status = AgentStepStatus.WAITING_APPROVAL
                    step.output_payload["approval_id"] = approval.approval_id
                    self._upsert_step(step)
                    run.status = AgentRunStatus.WAITING_APPROVAL
                    run.blocked_approval_id = approval.approval_id
                    summary["next_index"] = idx + 1
                    summary["blocked_step_id"] = step.step_id
                    run.summary = summary
                    self._upsert_run(run)
                    return run

                self._upsert_step(step)
            except Exception as exc:
                step.status = AgentStepStatus.FAILED
                step.error = str(exc)
                step.latency_ms = round((time.perf_counter() - started) * 1000, 2)
                step.compensation = {
                    "strategy": "compensate_and_continue",
                    "result": "logged_only",
                    "note": "No reversible compensation available for this action.",
                }
                self._upsert_step(step)
                run.errors.append(f"{action_type.value}: {exc}")

            summary["next_index"] = idx + 1
            summary.pop("blocked_step_id", None)
            run.summary = summary
            self._upsert_run(run)

        run.blocked_approval_id = None
        if run.errors:
            run.status = AgentRunStatus.COMPLETED_WITH_WARNINGS
        else:
            run.status = AgentRunStatus.COMPLETED
        self._upsert_run(run)
        return run

    async def run_objective(self, request: AgentRunRequest, tenant_id: str, actor: str, role: str) -> AgentRunResponse:
        run = AgentRunRecord(
            run_id=agent_os_state_store.next_run_id(),
            tenant_id=tenant_id,
            actor=actor,
            role=role,
            objective=request.objective,
            autonomy_level=request.autonomy_level,
            execution_mode=request.execution_mode,
            dry_run=request.dry_run,
            status=AgentRunStatus.PENDING,
            summary={
                "plan_actions": [action.value for action in self._build_plan(request.objective, request.max_steps)],
                "next_index": 0,
            },
        )
        self._upsert_run(run)
        run = await self._execute_run(run)
        return self._render_response(run.run_id)

    def get_run(self, run_id: str, tenant_id: str) -> AgentRunResponse:
        response = self._render_response(run_id)
        if response.run.tenant_id != tenant_id:
            raise KeyError(run_id)
        return response

    def list_runs(self, tenant_id: str, limit: int = 50) -> List[AgentRunRecord]:
        return [AgentRunRecord(**row) for row in agent_os_state_store.list_runs(tenant_id, limit=limit)]

    def list_pending_approvals(self, tenant_id: str, limit: int = 100) -> List[AgentApprovalRecord]:
        return [AgentApprovalRecord(**row) for row in agent_os_state_store.list_pending_approvals(tenant_id, limit=limit)]

    async def decide_approval(
        self,
        run_id: str,
        request: AgentApprovalDecisionRequest,
        tenant_id: str,
        actor: str,
        role: str,
    ) -> AgentRunResponse:
        approval = agent_os_state_store.get_approval(request.approval_id)
        if not approval or str(approval.get("run_id")) != run_id:
            raise KeyError(request.approval_id)
        run_row = agent_os_state_store.get_run(run_id)
        if not run_row:
            raise KeyError(run_id)
        run = AgentRunRecord(**run_row)
        if run.tenant_id != tenant_id:
            raise KeyError(run_id)

        approval_record = AgentApprovalRecord(**approval)
        approval_record.status = "approved" if request.approve else "rejected"
        approval_record.resolved_by = actor
        approval_record.resolved_at = datetime.now(timezone.utc)
        approval_record.note = request.note or approval_record.note
        self._upsert_approval(approval_record)

        if not request.approve:
            run.status = AgentRunStatus.FAILED
            run.errors.append(f"Approval rejected: {request.approval_id}")
            run.blocked_approval_id = None
            summary = dict(run.summary or {})
            summary.pop("blocked_step_id", None)
            run.summary = summary
            self._upsert_run(run)
            return self._render_response(run_id)

        run.actor = actor
        run.role = role
        run.blocked_approval_id = None
        summary = dict(run.summary or {})
        blocked_step_id = str(summary.get("blocked_step_id") or approval_record.step_id or "")
        blocked_step = agent_os_state_store.get_step(blocked_step_id) if blocked_step_id else None
        if blocked_step:
            approved_indices = [int(x) for x in (summary.get("approved_step_indices") or [])]
            idx = int(blocked_step.get("step_index") or 0)
            if idx not in approved_indices:
                approved_indices.append(idx)
            summary["approved_step_indices"] = approved_indices
            output_payload = blocked_step.get("output_payload") or {}
            has_executed_result = isinstance(output_payload, dict) and "result" in output_payload
            if has_executed_result:
                existing = AgentStepRecord(**blocked_step)
                existing.status = AgentStepStatus.COMPLETED
                self._upsert_step(existing)
                summary["next_index"] = idx + 1
            else:
                summary["next_index"] = idx
        summary.pop("blocked_step_id", None)
        run.summary = summary
        run = await self._execute_run(run)
        return self._render_response(run.run_id)

    def list_policies(self) -> List[AgentPolicyRule]:
        return [AgentPolicyRule(**row) for row in agent_os_state_store.list_policies()]

    def patch_policy(self, policy_id: str, request: AgentPolicyPatchRequest) -> AgentPolicyRule:
        rows = {row.policy_id: row for row in self.list_policies()}
        if policy_id not in rows:
            raise KeyError(policy_id)
        target = rows[policy_id]
        patch = request.model_dump(exclude_none=True)
        for key, value in patch.items():
            setattr(target, key, value)
        updated = agent_os_state_store.update_policy(policy_id, target.model_dump(mode="json"))
        if not updated:
            raise KeyError(policy_id)
        return AgentPolicyRule(**updated)

    def run_metrics(self, tenant_id: str) -> AgentRunMetrics:
        return AgentRunMetrics(**agent_os_state_store.metrics(tenant_id))

    def run_timeline(self, run_id: str, tenant_id: str) -> Dict[str, Any]:
        response = self.get_run(run_id, tenant_id=tenant_id)
        return {
            "run": response.run.model_dump(mode="json"),
            "steps": [row.model_dump(mode="json") for row in response.steps],
            "approvals": [row.model_dump(mode="json") for row in response.approvals],
        }


agent_os_service = AgentOSService()
