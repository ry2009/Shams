"""Persistent state store for Agent OS orchestration."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.models.agent_os import AgentActionType, AgentPolicyRule


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


class AgentOSStateStore:
    """SQLite-backed persistence for Agent OS runs, steps, and approvals."""

    def __init__(self) -> None:
        settings = get_settings()
        base = Path(settings.ops_state_path)
        self._db_path = base.with_name(f"{base.stem}.agent_os.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_sequences (
                    key_name TEXT PRIMARY KEY,
                    next_value INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant_updated
                    ON agent_runs (tenant_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS agent_steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_steps_run_index
                    ON agent_steps (run_id, step_index ASC);

                CREATE TABLE IF NOT EXISTS agent_approvals (
                    approval_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_approvals_run_status
                    ON agent_approvals (run_id, status);

                CREATE TABLE IF NOT EXISTS agent_policies (
                    policy_id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._seed_default_policies()
            self._conn.commit()

    def _seed_default_policies(self) -> None:
        defaults = [
            AgentPolicyRule(
                policy_id="policy.fleet.add_driver",
                action_type=AgentActionType.DRIVER_ADD,
                enabled=True,
                requires_admin_approval=False,
                destructive=False,
                min_confidence=0.95,
                max_targets=1,
                notes="Allow controlled driver creation for staffing updates.",
            ),
            AgentPolicyRule(
                policy_id="policy.fleet.remove_driver",
                action_type=AgentActionType.DRIVER_REMOVE,
                enabled=True,
                requires_admin_approval=True,
                destructive=True,
                min_confidence=0.99,
                max_targets=1,
                notes="Driver removal requires explicit approval and no active assignments.",
            ),
            AgentPolicyRule(
                policy_id="policy.dispatch.assign",
                action_type=AgentActionType.DISPATCH_ASSIGN,
                enabled=True,
                requires_admin_approval=False,
                destructive=False,
                min_confidence=0.85,
                max_targets=40,
                notes="Bulk assignment allowed in L3 when policy checks pass.",
            ),
            AgentPolicyRule(
                policy_id="policy.tickets.review",
                action_type=AgentActionType.TICKET_REVIEW,
                enabled=True,
                requires_admin_approval=False,
                destructive=False,
                min_confidence=0.9,
                max_targets=40,
                notes="Escalate to human when confidence/rule checks fail.",
            ),
            AgentPolicyRule(
                policy_id="policy.billing.export",
                action_type=AgentActionType.BILLING_EXPORT,
                enabled=True,
                requires_admin_approval=False,
                destructive=False,
                min_confidence=0.9,
                max_targets=40,
                notes="Allow ready-load billing export in autonomous runs.",
            ),
            AgentPolicyRule(
                policy_id="policy.system.reset",
                action_type=AgentActionType.SYSTEM_RESET,
                enabled=True,
                requires_admin_approval=True,
                destructive=True,
                min_confidence=0.99,
                max_targets=1,
                notes="Destructive action requires explicit admin approval.",
            ),
        ]
        for row in defaults:
            now = _utc_now_iso()
            self._conn.execute(
                """
                INSERT OR IGNORE INTO agent_policies (policy_id, action_type, data_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (row.policy_id, row.action_type.value, _json_dumps(row.model_dump(mode="json")), now),
            )

    def _next_sequence(self, key: str) -> int:
        row = self._conn.execute(
            "SELECT next_value FROM agent_sequences WHERE key_name = ?",
            (key,),
        ).fetchone()
        if row is None:
            current = 1
            self._conn.execute(
                "INSERT INTO agent_sequences (key_name, next_value) VALUES (?, ?)",
                (key, current + 1),
            )
            return current
        value = int(row["next_value"])
        self._conn.execute(
            "UPDATE agent_sequences SET next_value = ? WHERE key_name = ?",
            (value + 1, key),
        )
        return value

    def next_run_id(self) -> str:
        with self._lock:
            value = self._next_sequence("run")
            self._conn.commit()
        return f"ARUN-{value:06d}"

    def next_step_id(self) -> str:
        with self._lock:
            value = self._next_sequence("step")
            self._conn.commit()
        return f"ASTEP-{value:06d}"

    def next_approval_id(self) -> str:
        with self._lock:
            value = self._next_sequence("approval")
            self._conn.commit()
        return f"AAPR-{value:06d}"

    def upsert_run(self, run_id: str, tenant_id: str, status: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_runs (run_id, tenant_id, status, updated_at, data_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id)
                DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, data_json = excluded.data_json
                """,
                (run_id, tenant_id, status, now, _json_dumps(payload)),
            )
            self._conn.commit()
        return payload

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["data_json"])

    def list_runs(self, tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT data_json FROM agent_runs
                WHERE tenant_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (tenant_id, max(1, min(limit, 500))),
            ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def upsert_step(self, step_id: str, run_id: str, step_index: int, status: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_steps (step_id, run_id, step_index, status, updated_at, data_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(step_id)
                DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, data_json = excluded.data_json
                """,
                (step_id, run_id, step_index, status, now, _json_dumps(payload)),
            )
            self._conn.commit()
        return payload

    def list_steps(self, run_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT data_json FROM agent_steps
                WHERE run_id = ?
                ORDER BY step_index ASC
                """,
                (run_id,),
            ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def get_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM agent_steps WHERE step_id = ?",
                (step_id,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["data_json"])

    def upsert_approval(self, approval_id: str, run_id: str, step_id: str, status: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_approvals (approval_id, run_id, step_id, status, updated_at, data_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(approval_id)
                DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, data_json = excluded.data_json
                """,
                (approval_id, run_id, step_id, status, now, _json_dumps(payload)),
            )
            self._conn.commit()
        return payload

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM agent_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["data_json"])

    def list_approvals(self, run_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    """
                    SELECT data_json FROM agent_approvals
                    WHERE run_id = ? AND status = ?
                    ORDER BY updated_at DESC
                    """,
                    (run_id, status),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT data_json FROM agent_approvals
                    WHERE run_id = ?
                    ORDER BY updated_at DESC
                    """,
                    (run_id,),
                ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def list_pending_approvals(self, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT a.data_json
                FROM agent_approvals a
                JOIN agent_runs r ON r.run_id = a.run_id
                WHERE r.tenant_id = ? AND a.status = 'pending'
                ORDER BY a.updated_at DESC
                LIMIT ?
                """,
                (tenant_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def list_policies(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM agent_policies ORDER BY policy_id ASC",
            ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def get_policy_for_action(self, action_type: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT data_json FROM agent_policies
                WHERE action_type = ?
                ORDER BY policy_id ASC
                LIMIT 1
                """,
                (action_type,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["data_json"])

    def update_policy(self, policy_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        now = _utc_now_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM agent_policies WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                """
                UPDATE agent_policies
                SET data_json = ?, updated_at = ?
                WHERE policy_id = ?
                """,
                (_json_dumps(payload), now, policy_id),
            )
            self._conn.commit()
        return payload

    def metrics(self, tenant_id: str) -> Dict[str, Any]:
        runs = self.list_runs(tenant_id, limit=500)
        steps_total = 0
        steps_ok = 0
        latencies: List[float] = []
        waiting = 0
        failed = 0
        completed = 0

        for run in runs:
            status = str(run.get("status") or "")
            if status == "waiting_approval":
                waiting += 1
            elif status == "failed":
                failed += 1
            elif status in {"completed", "completed_with_warnings"}:
                completed += 1

            for step in self.list_steps(str(run.get("run_id") or "")):
                steps_total += 1
                if str(step.get("status") or "") == "completed":
                    steps_ok += 1
                latency = float(step.get("latency_ms") or 0.0)
                if latency > 0:
                    latencies.append(latency)

        latencies.sort()
        p95 = 0.0
        if latencies:
            idx = int(round(0.95 * (len(latencies) - 1)))
            p95 = float(latencies[idx])

        return {
            "runs_total": len(runs),
            "runs_completed": completed,
            "runs_waiting_approval": waiting,
            "runs_failed": failed,
            "step_success_rate": round(float(steps_ok) / max(1, steps_total), 4),
            "p95_step_latency_ms": round(p95, 2),
        }


agent_os_state_store = AgentOSStateStore()
