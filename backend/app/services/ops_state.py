"""SQLite-backed ops state store for SHAMS autonomous workflows."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.logging import logger
from app.models.ops import LoadRecord, LoadStatus


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class OpsStateStore:
    """Durable state manager for dispatch/ticketing/billing domains."""

    _lock_registry: dict[str, RLock] = {}
    _lock_registry_guard = Lock()

    def __init__(self) -> None:
        settings = get_settings()
        db_path = (settings.ops_db_path or "").strip()
        if not db_path:
            db_path = str(Path(settings.ops_state_path).with_suffix(".db"))

        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._mcleod_export_dir = Path(settings.mcleod_export_dir)
        self._mcleod_export_dir.mkdir(parents=True, exist_ok=True)
        self._lock = self._get_shared_lock(str(self._db_path.resolve()))
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._initialize_schema()

    @classmethod
    def _get_shared_lock(cls, key: str) -> RLock:
        with cls._lock_registry_guard:
            lock = cls._lock_registry.get(key)
            if lock is None:
                lock = RLock()
                cls._lock_registry[key] = lock
            return lock

    def _initialize_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sequences (
                    tenant_id TEXT NOT NULL,
                    key_name TEXT NOT NULL,
                    next_value INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, key_name)
                );

                CREATE TABLE IF NOT EXISTS loads (
                    tenant_id TEXT NOT NULL,
                    load_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, load_id)
                );

                CREATE TABLE IF NOT EXISTS reviews (
                    tenant_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    load_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, review_id)
                );

                CREATE INDEX IF NOT EXISTS idx_reviews_tenant_load ON reviews (tenant_id, load_id);
                CREATE INDEX IF NOT EXISTS idx_reviews_tenant_status ON reviews (tenant_id, status);

                CREATE TABLE IF NOT EXISTS billing (
                    tenant_id TEXT NOT NULL,
                    load_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, load_id)
                );

                CREATE INDEX IF NOT EXISTS idx_billing_tenant_status ON billing (tenant_id, status);

                CREATE TABLE IF NOT EXISTS timeline (
                    tenant_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    load_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, event_id)
                );

                CREATE INDEX IF NOT EXISTS idx_timeline_tenant_load ON timeline (tenant_id, load_id);
                CREATE INDEX IF NOT EXISTS idx_timeline_tenant_type ON timeline (tenant_id, event_type);
                CREATE INDEX IF NOT EXISTS idx_timeline_tenant_ts ON timeline (tenant_id, timestamp DESC);

                CREATE TABLE IF NOT EXISTS mcleod_exports (
                    tenant_id TEXT NOT NULL,
                    export_id TEXT NOT NULL,
                    load_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, export_id)
                );

                CREATE INDEX IF NOT EXISTS idx_exports_tenant_load ON mcleod_exports (tenant_id, load_id);

                CREATE TABLE IF NOT EXISTS dispatch_messages (
                    tenant_id TEXT NOT NULL,
                    dispatch_id TEXT NOT NULL,
                    load_id TEXT NOT NULL,
                    driver_id TEXT,
                    status TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, dispatch_id)
                );

                CREATE INDEX IF NOT EXISTS idx_dispatch_messages_tenant_load
                    ON dispatch_messages (tenant_id, load_id, sent_at DESC);

                CREATE TABLE IF NOT EXISTS drivers (
                    tenant_id TEXT NOT NULL,
                    driver_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, driver_id)
                );

                CREATE TABLE IF NOT EXISTS samsara_events (
                    tenant_id TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    load_id TEXT NOT NULL,
                    gps_miles REAL NOT NULL,
                    stop_events INTEGER NOT NULL,
                    vehicle_id TEXT,
                    window_start TEXT,
                    window_end TEXT,
                    captured_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, event_key)
                );

                CREATE INDEX IF NOT EXISTS idx_samsara_events_tenant_load_time
                    ON samsara_events (tenant_id, load_id, captured_at DESC);

                CREATE TABLE IF NOT EXISTS idempotency (
                    tenant_id TEXT NOT NULL,
                    key_name TEXT NOT NULL,
                    stored_at TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, key_name)
                );

                CREATE INDEX IF NOT EXISTS idx_idempotency_tenant_time ON idempotency (tenant_id, stored_at);

                CREATE TABLE IF NOT EXISTS automation_policies (
                    tenant_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, policy_id)
                );

                CREATE INDEX IF NOT EXISTS idx_automation_policies_tenant_status
                    ON automation_policies (tenant_id, status);

                CREATE TABLE IF NOT EXISTS outbound_messages (
                    tenant_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, message_id)
                );

                CREATE INDEX IF NOT EXISTS idx_outbound_messages_tenant_channel
                    ON outbound_messages (tenant_id, channel, created_at DESC);
                """
            )
            self._conn.commit()

    @staticmethod
    def _default_drivers() -> List[Dict[str, Any]]:
        return [
            {
                "driver_id": "DRV-101",
                "name": "Carlos Rodriguez",
                "truck_id": "F682",
                "trailer_id": "48124",
                "status": "available",
                "home_region": "FL-West",
                "assignment_count": 0,
            },
            {
                "driver_id": "DRV-102",
                "name": "Yoan Soto",
                "truck_id": "F336",
                "trailer_id": "48053",
                "status": "available",
                "home_region": "FL-Central",
                "assignment_count": 0,
            },
            {
                "driver_id": "DRV-103",
                "name": "Javier Morales",
                "truck_id": "F1471",
                "trailer_id": "48611",
                "status": "available",
                "home_region": "FL-South",
                "assignment_count": 0,
            },
            {
                "driver_id": "DRV-104",
                "name": "Roberto Diaz",
                "truck_id": "F516",
                "trailer_id": "48906",
                "status": "available",
                "home_region": "GA-Coastal",
                "assignment_count": 0,
            },
        ]

    @staticmethod
    def _default_sequence_start(key: str) -> int:
        if key == "load":
            return 1000
        return 1

    def _ensure_tenant_bootstrap(self, tenant_id: str) -> None:
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM drivers WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if row and int(row["c"]) > 0:
            return

        for driver in self._default_drivers():
            self._conn.execute(
                "INSERT OR IGNORE INTO drivers (tenant_id, driver_id, data_json) VALUES (?, ?, ?)",
                (tenant_id, driver["driver_id"], _json_dumps(driver)),
            )

    def next_sequence(self, tenant_id: str, key: str) -> int:
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            row = self._conn.execute(
                "SELECT next_value FROM sequences WHERE tenant_id = ? AND key_name = ?",
                (tenant_id, key),
            ).fetchone()
            if row is None:
                current = self._default_sequence_start(key)
                self._conn.execute(
                    "INSERT INTO sequences (tenant_id, key_name, next_value) VALUES (?, ?, ?)",
                    (tenant_id, key, current + 1),
                )
            else:
                current = int(row["next_value"])
                self._conn.execute(
                    "UPDATE sequences SET next_value = ? WHERE tenant_id = ? AND key_name = ?",
                    (current + 1, tenant_id, key),
                )
            self._conn.commit()
            return current

    def get_idempotent(self, tenant_id: str, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT response_json FROM idempotency WHERE tenant_id = ? AND key_name = ?",
                (tenant_id, key),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["response_json"])

    def set_idempotent(self, tenant_id: str, key: str, response: Dict[str, Any]) -> None:
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            self._conn.execute(
                """
                INSERT INTO idempotency (tenant_id, key_name, stored_at, response_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tenant_id, key_name)
                DO UPDATE SET stored_at = excluded.stored_at, response_json = excluded.response_json
                """,
                (tenant_id, key, _utc_now_iso(), _json_dumps(response)),
            )
            self._conn.execute(
                """
                DELETE FROM idempotency
                WHERE tenant_id = ?
                  AND key_name NOT IN (
                    SELECT key_name FROM idempotency
                    WHERE tenant_id = ?
                    ORDER BY stored_at DESC
                    LIMIT 10000
                  )
                """,
                (tenant_id, tenant_id),
            )
            self._conn.commit()

    def generate_load_id(self, tenant_id: str) -> str:
        return f"LOAD{self.next_sequence(tenant_id, 'load'):05d}"

    def reset_tenant_operational_data(self, tenant_id: str) -> None:
        """Clear mutable demo data so each seed starts from a clean scenario."""
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            for table in ("loads", "reviews", "billing", "timeline", "mcleod_exports", "samsara_events", "idempotency"):
                self._conn.execute(f"DELETE FROM {table} WHERE tenant_id = ?", (tenant_id,))
            for key in ("load", "review", "event", "export"):
                self._conn.execute(
                    """
                    INSERT INTO sequences (tenant_id, key_name, next_value)
                    VALUES (?, ?, 1)
                    ON CONFLICT(tenant_id, key_name)
                    DO UPDATE SET next_value = 1
                    """,
                    (tenant_id, key),
                )
            self._conn.commit()

    def upsert_load(self, tenant_id: str, load: LoadRecord) -> Dict[str, Any]:
        row = load.model_dump(mode="json")
        row["updated_at"] = _utc_now_iso()
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            self._conn.execute(
                """
                INSERT INTO loads (tenant_id, load_id, data_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tenant_id, load_id)
                DO UPDATE SET data_json = excluded.data_json, updated_at = excluded.updated_at
                """,
                (tenant_id, load.load_id, _json_dumps(row), row["updated_at"]),
            )
            self._conn.commit()
        return row

    def get_load(self, tenant_id: str, load_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM loads WHERE tenant_id = ? AND load_id = ?",
                (tenant_id, load_id),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["data_json"])

    def list_loads(self, tenant_id: str, status: Optional[LoadStatus] = None) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM loads WHERE tenant_id = ? ORDER BY updated_at DESC",
                (tenant_id,),
            ).fetchall()
        loads = [json.loads(row["data_json"]) for row in rows]
        if status:
            loads = [row for row in loads if row.get("status") == status.value]
        return loads

    def record_timeline_event(
        self,
        tenant_id: str,
        load_id: str,
        event_type: str,
        actor: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": f"EVT-{self.next_sequence(tenant_id, 'event'):06d}",
            "load_id": load_id,
            "event_type": event_type,
            "actor": actor,
            "timestamp": _utc_now_iso(),
            "details": details or {},
        }
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            self._conn.execute(
                """
                INSERT INTO timeline (tenant_id, event_id, load_id, event_type, actor, timestamp, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    event["event_id"],
                    load_id,
                    event_type,
                    actor,
                    event["timestamp"],
                    _json_dumps(event["details"]),
                ),
            )
            self._conn.execute(
                """
                DELETE FROM timeline
                WHERE tenant_id = ?
                  AND event_id NOT IN (
                    SELECT event_id FROM timeline
                    WHERE tenant_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 5000
                  )
                """,
                (tenant_id, tenant_id),
            )
            self._conn.commit()
        return event

    def list_timeline(self, tenant_id: str, load_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if load_id:
                rows = self._conn.execute(
                    """
                    SELECT event_id, load_id, event_type, actor, timestamp, details_json
                    FROM timeline
                    WHERE tenant_id = ? AND load_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 300
                    """,
                    (tenant_id, load_id),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT event_id, load_id, event_type, actor, timestamp, details_json
                    FROM timeline
                    WHERE tenant_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 300
                    """,
                    (tenant_id,),
                ).fetchall()

        return [
            {
                "event_id": row["event_id"],
                "load_id": row["load_id"],
                "event_type": row["event_type"],
                "actor": row["actor"],
                "timestamp": row["timestamp"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def list_drivers(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            rows = self._conn.execute(
                "SELECT data_json FROM drivers WHERE tenant_id = ? ORDER BY driver_id",
                (tenant_id,),
            ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def _save_driver(self, tenant_id: str, driver: Dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO drivers (tenant_id, driver_id, data_json)
            VALUES (?, ?, ?)
            ON CONFLICT(tenant_id, driver_id)
            DO UPDATE SET data_json = excluded.data_json
            """,
            (tenant_id, driver["driver_id"], _json_dumps(driver)),
        )

    def set_driver_status(self, tenant_id: str, driver_id: str, status: str) -> None:
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            row = self._conn.execute(
                "SELECT data_json FROM drivers WHERE tenant_id = ? AND driver_id = ?",
                (tenant_id, driver_id),
            ).fetchone()
            if row:
                driver = json.loads(row["data_json"])
                driver["status"] = status
                self._save_driver(tenant_id, driver)
                self._conn.commit()

    def create_driver(
        self,
        tenant_id: str,
        *,
        name: str,
        truck_id: Optional[str] = None,
        trailer_id: Optional[str] = None,
        home_region: str = "FL-West",
    ) -> Dict[str, Any]:
        cleaned_name = " ".join(str(name or "").split()).strip()
        if len(cleaned_name) < 3:
            raise ValueError("Driver name must be at least 3 characters.")

        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            drivers = self.list_drivers(tenant_id)
            for row in drivers:
                if str(row.get("name") or "").strip().lower() == cleaned_name.lower():
                    return {"created": False, "driver": row, "reason": "driver already exists"}

            existing_ids = {str(row.get("driver_id") or "") for row in drivers}
            while True:
                seq = self.next_sequence(tenant_id, "driver")
                driver_id = f"DRV-{200 + seq:03d}"
                if driver_id not in existing_ids:
                    break

            suffix = str(600 + seq)
            driver = {
                "driver_id": driver_id,
                "name": cleaned_name,
                "truck_id": truck_id or f"F{suffix}",
                "trailer_id": trailer_id or str(48000 + seq),
                "status": "available",
                "home_region": home_region or "FL-West",
                "assignment_count": 0,
            }
            self._save_driver(tenant_id, driver)
            self._conn.commit()
            return {"created": True, "driver": driver, "reason": "driver added"}

    def remove_driver(self, tenant_id: str, *, driver_ref: str) -> Dict[str, Any]:
        ref = str(driver_ref or "").strip()
        if not ref:
            raise ValueError("driver_ref is required")

        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            drivers = self.list_drivers(tenant_id)
            target = None
            ref_norm = " ".join(ref.lower().split())
            for row in drivers:
                driver_id = str(row.get("driver_id") or "").strip()
                name = " ".join(str(row.get("name") or "").lower().split())
                if ref.upper() == driver_id.upper() or ref_norm == name:
                    target = row
                    break
            if not target:
                return {"removed": False, "reason": f"driver '{ref}' not found", "driver": None}

            target_id = str(target.get("driver_id") or "")
            assigned_loads = []
            for load in self.list_loads(tenant_id):
                assignment = load.get("assignment") or {}
                assigned_driver_id = str(assignment.get("driver_id") or "")
                status = str(load.get("status") or "").lower()
                if assigned_driver_id == target_id and status != LoadStatus.DELIVERED.value:
                    assigned_loads.append(str(load.get("load_id") or ""))
            if assigned_loads:
                return {
                    "removed": False,
                    "reason": f"driver has active loads: {', '.join(assigned_loads[:5])}",
                    "driver": target,
                }

            self._conn.execute(
                "DELETE FROM drivers WHERE tenant_id = ? AND driver_id = ?",
                (tenant_id, target_id),
            )
            self._conn.commit()
            return {"removed": True, "reason": "driver removed", "driver": target}

    def reset_driver_pool(self, tenant_id: str) -> None:
        """Reset driver availability so each demo run starts from a clean dispatch state."""
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            rows = self._conn.execute(
                "SELECT data_json FROM drivers WHERE tenant_id = ? ORDER BY driver_id",
                (tenant_id,),
            ).fetchall()
            for row in rows:
                driver = json.loads(row["data_json"])
                driver["status"] = "available"
                driver["assignment_count"] = 0
                self._save_driver(tenant_id, driver)
            self._conn.commit()

    @staticmethod
    def _region_hint_from_pickup(pickup_location: str) -> str:
        text = (pickup_location or "").lower()
        if any(token in text for token in ["tampa", "plant", "polk"]):
            return "FL-Central"
        if any(token in text for token in ["naples", "ft myers", "fort myers", "cape"]):
            return "FL-West"
        if any(token in text for token in ["miami", "broward", "palm"]):
            return "FL-South"
        if any(token in text for token in ["savannah", "rincon", "ga"]):
            return "GA-Coastal"
        return ""

    def auto_assign_load(self, tenant_id: str, load_id: str) -> Dict[str, Any]:
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            load = self.get_load(tenant_id, load_id)
            if not load:
                raise KeyError(load_id)

            drivers = self.list_drivers(tenant_id)
            if not drivers:
                raise RuntimeError("No drivers configured")

            preferred_region = self._region_hint_from_pickup(load.get("pickup_location", ""))

            def score(driver: Dict[str, Any]) -> tuple:
                status_rank = 0 if driver.get("status") == "available" else 1
                region_rank = 0 if preferred_region and driver.get("home_region") == preferred_region else 1
                assignments = int(driver.get("assignment_count") or 0)
                return (status_rank, region_rank, assignments, driver.get("driver_id", ""))

            chosen = sorted(drivers, key=score)[0]
            chosen["status"] = "assigned"
            chosen["assignment_count"] = int(chosen.get("assignment_count") or 0) + 1
            self._save_driver(tenant_id, chosen)

            assignment = {
                "driver_id": chosen.get("driver_id"),
                "driver_name": chosen.get("name"),
                "truck_id": chosen.get("truck_id"),
                "trailer_id": chosen.get("trailer_id"),
                "assigned_at": _utc_now_iso(),
                "mode": "autonomous",
            }
            load["assignment"] = assignment
            load["status"] = LoadStatus.ASSIGNED.value
            load["version"] = int(load.get("version") or 1) + 1
            load["updated_at"] = _utc_now_iso()
            self._conn.execute(
                """
                INSERT INTO loads (tenant_id, load_id, data_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tenant_id, load_id)
                DO UPDATE SET data_json = excluded.data_json, updated_at = excluded.updated_at
                """,
                (tenant_id, load_id, _json_dumps(load), load["updated_at"]),
            )
            self._conn.commit()
        return assignment

    def assign_load(
        self,
        tenant_id: str,
        load_id: str,
        driver_id: str,
        truck_id: Optional[str],
        trailer_id: Optional[str],
        mode: str = "manual",
    ) -> Dict[str, Any]:
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            load = self.get_load(tenant_id, load_id)
            if not load:
                raise KeyError(load_id)

            driver_row = self._conn.execute(
                "SELECT data_json FROM drivers WHERE tenant_id = ? AND driver_id = ?",
                (tenant_id, driver_id),
            ).fetchone()
            driver = json.loads(driver_row["data_json"]) if driver_row else {"driver_id": driver_id, "name": driver_id}
            driver["status"] = "assigned"
            driver["assignment_count"] = int(driver.get("assignment_count") or 0) + 1
            self._save_driver(tenant_id, driver)

            assignment = {
                "driver_id": driver_id,
                "driver_name": driver.get("name", driver_id),
                "truck_id": truck_id or driver.get("truck_id"),
                "trailer_id": trailer_id or driver.get("trailer_id"),
                "assigned_at": _utc_now_iso(),
                "mode": mode,
            }
            load["assignment"] = assignment
            load["status"] = LoadStatus.ASSIGNED.value
            load["version"] = int(load.get("version") or 1) + 1
            load["updated_at"] = _utc_now_iso()
            self._conn.execute(
                """
                INSERT INTO loads (tenant_id, load_id, data_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tenant_id, load_id)
                DO UPDATE SET data_json = excluded.data_json, updated_at = excluded.updated_at
                """,
                (tenant_id, load_id, _json_dumps(load), load["updated_at"]),
            )
            self._conn.commit()
        return assignment

    def store_review(self, tenant_id: str, review: Dict[str, Any]) -> Dict[str, Any]:
        created_at = review.get("created_at") or _utc_now_iso()
        status = review.get("status", "exception")
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            self._conn.execute(
                """
                INSERT INTO reviews (tenant_id, review_id, load_id, status, created_at, data_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, review_id)
                DO UPDATE SET status = excluded.status, data_json = excluded.data_json
                """,
                (
                    tenant_id,
                    review["review_id"],
                    review["load_id"],
                    status,
                    created_at,
                    _json_dumps(review),
                ),
            )
            billing = {
                "load_id": review["load_id"],
                "status": "ready" if review.get("billing_ready") else "needs_review",
                "billing_ready": bool(review.get("billing_ready")),
                "ready_reason": review.get("approval_reason", ""),
                "required_documents": ["rate_confirmation", "bill_of_lading", "proof_of_delivery"],
                "missing_documents": review.get("missing_documents", []),
                "leakage_findings": review.get("leakage_findings", []),
                "updated_at": _utc_now_iso(),
            }
            self._conn.execute(
                """
                INSERT INTO billing (tenant_id, load_id, status, updated_at, data_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, load_id)
                DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, data_json = excluded.data_json
                """,
                (tenant_id, review["load_id"], billing["status"], billing["updated_at"], _json_dumps(billing)),
            )
            self._conn.commit()
        return review

    def list_reviews(self, tenant_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    """
                    SELECT data_json FROM reviews
                    WHERE tenant_id = ? AND status = ?
                    ORDER BY created_at DESC
                    """,
                    (tenant_id, status),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT data_json FROM reviews
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC
                    """,
                    (tenant_id,),
                ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def get_review(self, tenant_id: str, review_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM reviews WHERE tenant_id = ? AND review_id = ?",
                (tenant_id, review_id),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["data_json"])

    def set_review_status(self, tenant_id: str, review_id: str, status: str, note: str = "") -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM reviews WHERE tenant_id = ? AND review_id = ?",
                (tenant_id, review_id),
            ).fetchone()
            if not row:
                raise KeyError(review_id)

            review = json.loads(row["data_json"])
            review["status"] = status
            review["approval_reason"] = note or review.get("approval_reason", "")
            review["updated_at"] = _utc_now_iso()
            self._conn.execute(
                """
                UPDATE reviews
                SET status = ?, data_json = ?
                WHERE tenant_id = ? AND review_id = ?
                """,
                (status, _json_dumps(review), tenant_id, review_id),
            )

            billing_row = self._conn.execute(
                "SELECT data_json FROM billing WHERE tenant_id = ? AND load_id = ?",
                (tenant_id, review["load_id"]),
            ).fetchone()
            billing = json.loads(billing_row["data_json"]) if billing_row else {"load_id": review["load_id"]}
            billing["status"] = "ready" if status in {"approved", "resolved"} else "needs_review"
            billing["billing_ready"] = status in {"approved", "resolved"}
            billing["ready_reason"] = note or "manual override"
            billing["updated_at"] = _utc_now_iso()

            self._conn.execute(
                """
                INSERT INTO billing (tenant_id, load_id, status, updated_at, data_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, load_id)
                DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, data_json = excluded.data_json
                """,
                (tenant_id, review["load_id"], billing["status"], billing["updated_at"], _json_dumps(billing)),
            )
            self._conn.commit()
        return review

    def list_billing(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM billing WHERE tenant_id = ? ORDER BY updated_at DESC",
                (tenant_id,),
            ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def add_export(self, tenant_id: str, load_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        export_id = f"EXP-{self.next_sequence(tenant_id, 'export'):06d}"
        tenant_dir = self._mcleod_export_dir / tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)
        artifact = tenant_dir / f"{export_id}_{load_id}.json"
        artifact.write_text(_json_dumps(payload), encoding="utf-8")

        row = {
            "export_id": export_id,
            "load_id": load_id,
            "status": "generated",
            "artifact_path": str(artifact),
            "generated_at": _utc_now_iso(),
            "payload_preview": {"load_id": load_id, "keys": sorted(payload.keys())},
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO mcleod_exports (tenant_id, export_id, load_id, status, generated_at, data_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, export_id)
                DO UPDATE SET status = excluded.status, data_json = excluded.data_json
                """,
                (tenant_id, export_id, load_id, row["status"], row["generated_at"], _json_dumps(row)),
            )
            self._conn.commit()
        return row

    def list_exports(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM mcleod_exports WHERE tenant_id = ? ORDER BY generated_at DESC",
                (tenant_id,),
            ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def replay_export(self, tenant_id: str, export_id: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM mcleod_exports WHERE tenant_id = ? AND export_id = ?",
                (tenant_id, export_id),
            ).fetchone()
            if not row:
                raise KeyError(export_id)
            payload = json.loads(row["data_json"])
            payload["status"] = "replayed"
            payload["replayed_at"] = _utc_now_iso()
            self._conn.execute(
                """
                UPDATE mcleod_exports
                SET status = ?, data_json = ?
                WHERE tenant_id = ? AND export_id = ?
                """,
                (payload["status"], _json_dumps(payload), tenant_id, export_id),
            )
            self._conn.commit()
        return payload

    def add_dispatch_message(self, tenant_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        dispatch_id = f"DSP-{self.next_sequence(tenant_id, 'dispatch'):06d}"
        now = _utc_now_iso()
        row = {
            "dispatch_id": dispatch_id,
            "load_id": str(payload.get("load_id") or ""),
            "driver_id": str(payload.get("driver_id") or ""),
            "status": str(payload.get("status") or "sent"),
            "sent_at": now,
            "channel": str(payload.get("channel") or "driver_app"),
            "payload": payload,
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO dispatch_messages (tenant_id, dispatch_id, load_id, driver_id, status, sent_at, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, dispatch_id)
                DO UPDATE SET status = excluded.status, sent_at = excluded.sent_at, data_json = excluded.data_json
                """,
                (
                    tenant_id,
                    dispatch_id,
                    row["load_id"],
                    row["driver_id"],
                    row["status"],
                    row["sent_at"],
                    _json_dumps(row),
                ),
            )
            self._conn.commit()
        return row

    def list_dispatch_messages(self, tenant_id: str, load_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            if load_id:
                rows = self._conn.execute(
                    """
                    SELECT data_json FROM dispatch_messages
                    WHERE tenant_id = ? AND load_id = ?
                    ORDER BY sent_at DESC
                    LIMIT ?
                    """,
                    (tenant_id, str(load_id).strip().upper(), max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT data_json FROM dispatch_messages
                    WHERE tenant_id = ?
                    ORDER BY sent_at DESC
                    LIMIT ?
                    """,
                    (tenant_id, max(1, min(limit, 500))),
                ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def upsert_automation_policy(self, tenant_id: str, policy_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now_iso()
        row = dict(payload or {})
        row["policy_id"] = policy_id
        row["updated_at"] = now
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO automation_policies (tenant_id, policy_id, status, updated_at, data_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, policy_id)
                DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, data_json = excluded.data_json
                """,
                (
                    tenant_id,
                    policy_id,
                    str(row.get("status") or "active"),
                    now,
                    _json_dumps(row),
                ),
            )
            self._conn.commit()
        return row

    def get_automation_policy(self, tenant_id: str, policy_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM automation_policies WHERE tenant_id = ? AND policy_id = ?",
                (tenant_id, policy_id),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["data_json"])

    def list_automation_policies(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM automation_policies WHERE tenant_id = ? ORDER BY updated_at DESC",
                (tenant_id,),
            ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def add_outbound_message(
        self,
        tenant_id: str,
        *,
        channel: str,
        recipient: str,
        payload: Dict[str, Any],
        status: str = "queued",
    ) -> Dict[str, Any]:
        message_id = f"MSG-{self.next_sequence(tenant_id, 'outbound'):06d}"
        now = _utc_now_iso()
        row = {
            "message_id": message_id,
            "channel": str(channel or "unknown"),
            "recipient": str(recipient or "unknown"),
            "status": str(status or "queued"),
            "created_at": now,
            "payload": payload or {},
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO outbound_messages (tenant_id, message_id, channel, recipient, status, created_at, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, message_id)
                DO UPDATE SET status = excluded.status, data_json = excluded.data_json
                """,
                (
                    tenant_id,
                    message_id,
                    row["channel"],
                    row["recipient"],
                    row["status"],
                    row["created_at"],
                    _json_dumps(row),
                ),
            )
            self._conn.commit()
        return row

    def list_outbound_messages(
        self,
        tenant_id: str,
        *,
        channel: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            if channel:
                rows = self._conn.execute(
                    """
                    SELECT data_json FROM outbound_messages
                    WHERE tenant_id = ? AND channel = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (tenant_id, channel, max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT data_json FROM outbound_messages
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (tenant_id, max(1, min(limit, 500))),
                ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def ingest_samsara_events(self, tenant_id: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        inserted = 0
        skipped = 0
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            for event in events:
                if not isinstance(event, dict):
                    skipped += 1
                    continue

                load_id = str(event.get("load_id", "")).strip().upper()
                if not load_id:
                    skipped += 1
                    continue

                gps_raw = event.get("gps_miles")
                try:
                    gps_miles = float(gps_raw)
                except Exception:
                    skipped += 1
                    continue
                if gps_miles < 0:
                    skipped += 1
                    continue

                captured_dt = _parse_iso_utc(event.get("event_time")) or datetime.now(timezone.utc)
                captured_at = captured_dt.isoformat()
                vehicle_id = str(event.get("vehicle_id", "")).strip() or None
                stop_events = int(event.get("stop_events") or 0)
                window_start = str(event.get("window_start", "")).strip() or None
                window_end = str(event.get("window_end", "")).strip() or None
                event_key = f"{load_id}|{vehicle_id or '-'}|{captured_at}|{gps_miles:.3f}"

                cursor = self._conn.execute(
                    """
                    INSERT INTO samsara_events (
                        tenant_id, event_key, load_id, gps_miles, stop_events, vehicle_id,
                        window_start, window_end, captured_at, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, event_key)
                    DO NOTHING
                    """,
                    (
                        tenant_id,
                        event_key,
                        load_id,
                        gps_miles,
                        stop_events,
                        vehicle_id,
                        window_start,
                        window_end,
                        captured_at,
                        _json_dumps(event),
                    ),
                )
                if cursor.rowcount and cursor.rowcount > 0:
                    inserted += 1

            self._conn.execute(
                """
                DELETE FROM samsara_events
                WHERE tenant_id = ?
                  AND event_key NOT IN (
                    SELECT event_key FROM samsara_events
                    WHERE tenant_id = ?
                    ORDER BY captured_at DESC
                    LIMIT 50000
                  )
                """,
                (tenant_id, tenant_id),
            )
            self._conn.commit()
        return {"ingested": inserted, "skipped": skipped}

    def query_samsara_events(
        self,
        tenant_id: str,
        load_ids: List[str],
        hours_back: int,
    ) -> List[Dict[str, Any]]:
        normalized_loads = [str(load_id).strip().upper() for load_id in load_ids if str(load_id).strip()]
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours_back)))).isoformat()
        with self._lock:
            if normalized_loads:
                placeholders = ",".join("?" for _ in normalized_loads)
                sql = (
                    "SELECT load_id, gps_miles, stop_events, vehicle_id, window_start, window_end, captured_at "
                    f"FROM samsara_events WHERE tenant_id = ? AND captured_at >= ? AND load_id IN ({placeholders}) "
                    "ORDER BY captured_at DESC LIMIT 2000"
                )
                rows = self._conn.execute(sql, (tenant_id, cutoff, *normalized_loads)).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT load_id, gps_miles, stop_events, vehicle_id, window_start, window_end, captured_at
                    FROM samsara_events
                    WHERE tenant_id = ? AND captured_at >= ?
                    ORDER BY captured_at DESC
                    LIMIT 2000
                    """,
                    (tenant_id, cutoff),
                ).fetchall()

        return [
            {
                "load_id": row["load_id"],
                "gps_miles": float(row["gps_miles"]),
                "stop_events": int(row["stop_events"]),
                "vehicle_id": row["vehicle_id"],
                "window_start": row["window_start"],
                "window_end": row["window_end"],
                "event_time": row["captured_at"],
            }
            for row in rows
        ]

    def latest_samsara_miles(self, tenant_id: str, load_id: str, hours_back: int = 72) -> float | None:
        normalized = str(load_id).strip().upper()
        if not normalized:
            return None
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours_back)))).isoformat()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT gps_miles
                FROM samsara_events
                WHERE tenant_id = ? AND load_id = ? AND captured_at >= ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (tenant_id, normalized, cutoff),
            ).fetchone()
        if not row:
            return None
        return float(row["gps_miles"])

    def seed_synthetic_scenario(self, tenant_id: str, *, seed: int, loads: int, exception_ratio: float) -> Dict[str, Any]:
        import random

        random.seed(seed)
        customers = [
            "LATCRETE INTERNATIONAL INC",
            "LEHIGH CEMENT COMPANY",
            "A-1 BLOCK CORPORATION",
            "ASHGROVE CEMENT",
            "VERANZA GROUP FT MYERS",
        ]
        brokers = ["Coyote", "TQL", "Landstar", "JB Hunt", "Convoy"]
        pickup_sites = ["Tampa Plant", "Cape Coral Site", "Fort Myers Yard", "Naples Transfer"]
        drop_sites = ["Jobsite North", "Jobsite South", "Warehouse A", "Warehouse B"]

        created = []
        with self._lock:
            self._ensure_tenant_bootstrap(tenant_id)
            for _ in range(loads):
                load_id = f"LOAD{self.next_sequence(tenant_id, 'load'):05d}"
                planned_miles = round(random.uniform(18, 240), 1)
                rate = round(planned_miles * random.uniform(2.6, 4.3), 2)
                row = {
                    "load_id": load_id,
                    "customer": random.choice(customers),
                    "broker": random.choice(brokers),
                    "pickup_location": random.choice(pickup_sites),
                    "delivery_location": random.choice(drop_sites),
                    "pickup_time": f"2026-02-{random.randint(10, 28):02d}T{random.randint(5, 11):02d}:00:00",
                    "delivery_time": f"2026-02-{random.randint(10, 28):02d}T{random.randint(12, 22):02d}:00:00",
                    "equipment_type": "bulk",
                    "planned_miles": planned_miles,
                    "rate_total": rate,
                    "zone": f"FL-Z{random.randint(1, 9)}",
                    "priority": random.choice(["normal", "high"]),
                    "notes": "synthetic_seed",
                    "source": "synthetic",
                    "status": LoadStatus.PLANNED.value,
                    "assignment": {},
                    "version": 1,
                    "created_at": _utc_now_iso(),
                    "updated_at": _utc_now_iso(),
                }
                self._conn.execute(
                    """
                    INSERT INTO loads (tenant_id, load_id, data_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(tenant_id, load_id)
                    DO UPDATE SET data_json = excluded.data_json, updated_at = excluded.updated_at
                    """,
                    (tenant_id, load_id, _json_dumps(row), row["updated_at"]),
                )

                maybe_exception = random.random() < exception_ratio
                review_id = f"REV-{self.next_sequence(tenant_id, 'review'):06d}"
                failed_rules = (
                    ["docs.required (block): Upload missing source docs before billing"]
                    if maybe_exception
                    else []
                )
                review = {
                    "review_id": review_id,
                    "load_id": load_id,
                    "ticket_number": f"TKT-{random.randint(10000000000, 99999999999)}",
                    "status": "exception" if maybe_exception else "approved",
                    "auto_approved": not maybe_exception,
                    "approval_reason": (
                        "Blocking validation failure: docs.required | Missing docs: proof_of_delivery"
                        if maybe_exception
                        else "Auto-approved: confidence and validation thresholds passed"
                    ),
                    "final_confidence": 0.78 if maybe_exception else 0.992,
                    "confidence_profile": [],
                    "rules": [],
                    "failed_rules": failed_rules,
                    "leakage_findings": ["Possible zone mismatch"] if maybe_exception else [],
                    "billing_ready": not maybe_exception,
                    "processing_time_ms": round(random.uniform(1200, 6400), 2),
                    "documents_used": [],
                    "missing_documents": ["proof_of_delivery"] if maybe_exception else [],
                    "created_at": _utc_now_iso(),
                }
                self._conn.execute(
                    """
                    INSERT INTO reviews (tenant_id, review_id, load_id, status, created_at, data_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, review_id)
                    DO UPDATE SET status = excluded.status, data_json = excluded.data_json
                    """,
                    (tenant_id, review_id, load_id, review["status"], review["created_at"], _json_dumps(review)),
                )
                billing = {
                    "load_id": load_id,
                    "status": "needs_review" if maybe_exception else "ready",
                    "billing_ready": not maybe_exception,
                    "ready_reason": "seeded",
                    "required_documents": ["rate_confirmation", "bill_of_lading", "proof_of_delivery"],
                    "missing_documents": ["proof_of_delivery"] if maybe_exception else [],
                    "leakage_findings": review["leakage_findings"],
                    "updated_at": _utc_now_iso(),
                }
                self._conn.execute(
                    """
                    INSERT INTO billing (tenant_id, load_id, status, updated_at, data_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, load_id)
                    DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, data_json = excluded.data_json
                    """,
                    (tenant_id, load_id, billing["status"], billing["updated_at"], _json_dumps(billing)),
                )

                created.append(load_id)

            self._conn.commit()

        return {
            "loads_created": len(created),
            "exception_ratio": exception_ratio,
            "load_ids": created,
        }

    def metrics_snapshot(self, tenant_id: str) -> Dict[str, Any]:
        loads = self.list_loads(tenant_id)
        reviews = self.list_reviews(tenant_id)
        billing = self.list_billing(tenant_id)

        latencies = [float(row.get("processing_time_ms") or 0.0) for row in reviews if row.get("processing_time_ms") is not None]
        latencies.sort()

        with self._lock:
            timeline_rows = self._conn.execute(
                """
                SELECT details_json FROM timeline
                WHERE tenant_id = ? AND event_type = 'load_assigned'
                """,
                (tenant_id,),
            ).fetchall()
        total_assignments = len(timeline_rows)
        auto_assignments = 0
        for row in timeline_rows:
            details = json.loads(row["details_json"])
            if details.get("mode") == "autonomous":
                auto_assignments += 1

        def pct(values: List[float], p: float) -> float:
            if not values:
                return 0.0
            idx = min(len(values) - 1, int(round((len(values) - 1) * p)))
            return values[idx]

        delivered = sum(1 for row in loads if row.get("status") == LoadStatus.DELIVERED.value)
        auto_approved = sum(1 for row in reviews if row.get("auto_approved"))
        ready_count = sum(1 for row in billing if row.get("billing_ready"))
        leakage_findings = sum(len(row.get("leakage_findings") or []) for row in reviews)

        return {
            "active_loads": sum(1 for row in loads if row.get("status") != LoadStatus.DELIVERED.value),
            "delivered_loads": delivered,
            "auto_assignment_rate": round(auto_assignments / max(1, total_assignments), 4),
            "tickets_reviewed": len(reviews),
            "auto_approval_rate": round(auto_approved / max(1, len(reviews)), 4),
            "exception_rate": round(
                sum(1 for row in reviews if row.get("status") == "exception") / max(1, len(reviews)),
                4,
            ),
            "billing_ready_rate": round(ready_count / max(1, len(billing)), 4),
            "estimated_leakage_recovered_usd": round(75.0 * leakage_findings, 2),
            "avg_review_latency_ms": round(sum(latencies) / max(1, len(latencies)), 2),
            "p95_review_latency_ms": round(pct(latencies, 0.95), 2),
            "counts_by_status": dict(Counter(row.get("status", "planned") for row in loads)),
        }


ops_state_store = OpsStateStore()
