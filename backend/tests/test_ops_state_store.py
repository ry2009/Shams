"""Unit tests for ops state persistence and KPI calculations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import sys
from pathlib import Path


TMP = Path(__file__).resolve().parent / ".tmp_state"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["OPS_STATE_PATH"] = str(TMP / "ops_state.json")
os.environ["MCLEOD_EXPORT_DIR"] = str(TMP / "mcleod_exports")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.ops import LoadRecord  # noqa: E402
from app.services.ops_state import OpsStateStore  # noqa: E402


def test_seed_and_metrics_snapshot_has_expected_keys():
    store = OpsStateStore()
    result = store.seed_synthetic_scenario("demo_test", seed=7, loads=6, exception_ratio=0.25)
    assert result["loads_created"] == 6

    snapshot = store.metrics_snapshot("demo_test")
    assert "active_loads" in snapshot
    assert "auto_approval_rate" in snapshot
    assert snapshot["tickets_reviewed"] >= 6


def test_load_upsert_assignment_and_export_lifecycle():
    store = OpsStateStore()
    tenant = "demo_assign"
    load_id = store.generate_load_id(tenant)

    load = LoadRecord(
        load_id=load_id,
        customer="A-1 BLOCK",
        pickup_location="Fort Myers",
        delivery_location="Naples",
        planned_miles=42,
        rate_total=210.0,
    )
    row = store.upsert_load(tenant, load)
    assert row["load_id"] == load_id

    assignment = store.auto_assign_load(tenant, load_id)
    assert assignment["mode"] == "autonomous"

    export = store.add_export(tenant, load_id, {"load_id": load_id, "ticket": "TKT-123"})
    assert export["status"] == "generated"
    assert Path(export["artifact_path"]).exists()

    replay = store.replay_export(tenant, export["export_id"])
    assert replay["status"] == "replayed"


def test_concurrent_sequence_generation_is_unique():
    store = OpsStateStore()
    tenant = "concurrency"

    def _next() -> str:
        return store.generate_load_id(tenant)

    with ThreadPoolExecutor(max_workers=12) as pool:
        load_ids = list(pool.map(lambda _: _next(), range(200)))

    assert len(load_ids) == 200
    assert len(set(load_ids)) == 200


def test_samsara_ingest_query_and_latest_miles():
    store = OpsStateStore()
    tenant = "telemetry"
    payload = [
        {"load_id": "load001", "gps_miles": 88.3, "stop_events": 2, "vehicle_id": "V1"},
        {"load_id": "LOAD001", "gps_miles": 90.1, "stop_events": 3, "vehicle_id": "V1"},
        {"load_id": "LOAD002", "gps_miles": 41.7, "stop_events": 1, "vehicle_id": "V2"},
    ]
    result = store.ingest_samsara_events(tenant, payload)
    assert result["ingested"] >= 2

    events = store.query_samsara_events(tenant, ["load001"], hours_back=24)
    assert len(events) >= 1
    assert all(event["load_id"] == "LOAD001" for event in events)

    latest = store.latest_samsara_miles(tenant, "load001", hours_back=24)
    assert latest is not None
    assert latest >= 88.3
