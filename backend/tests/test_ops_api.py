"""API-level tests for SHAMS autonomous ops router."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


TMP = Path(__file__).resolve().parent / ".tmp_ops"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["OPENAI_API_KEY"] = ""
os.environ["OPENAI_BASE_URL"] = ""
os.environ["TINKER_MODEL_PATH"] = ""
os.environ["SAMSARA_API_TOKEN"] = "adapter-token"
os.environ["SAMSARA_EVENTS_URL"] = ""
os.environ["CHROMA_DB_PATH"] = str(TMP / "chroma")
os.environ["UPLOAD_DIR"] = str(TMP / "uploads")
os.environ["DOCUMENT_REGISTRY_PATH"] = str(TMP / "document_registry.json")
os.environ["OPS_STATE_PATH"] = str(TMP / "ops_state.json")
os.environ["MCLEOD_EXPORT_DIR"] = str(TMP / "mcleod_exports")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.models.ops import CopilotQueryResponse  # noqa: E402
from app.services.ops_engine import ops_engine  # noqa: E402


client = TestClient(app)


def _seed_and_get_load_id() -> str:
    payload = {"seed": 99, "loads": 4, "include_exceptions_ratio": 0.25}
    response = client.post("/ops/seed/synthetic", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["loads_created"] == 4
    return data["load_ids"][0]


def test_dispatch_board_and_auto_assign_flow():
    load_id = _seed_and_get_load_id()

    board = client.get("/ops/dispatch/board")
    assert board.status_code == 200
    board_data = board.json()
    assert len(board_data["loads"]) >= 1

    assign = client.post("/ops/dispatch/assign", json={"load_id": load_id, "auto": True})
    assert assign.status_code == 200
    assignment = assign.json()
    assert assignment["driver_id"].startswith("DRV-")

    timeline = client.get(f"/ops/loads/{load_id}/timeline")
    assert timeline.status_code == 200
    assert len(timeline.json()["events"]) >= 1


def test_driver_app_dispatch_send_and_feed():
    seeded = client.post(
        "/ops/seed/demo-pack",
        json={"seed": 52, "loads": 5, "docs_per_load": 3, "include_exceptions_ratio": 0.0, "index_documents": False},
    )
    assert seeded.status_code == 200
    load_id = seeded.json()["load_ids"][0]

    assign = client.post("/ops/dispatch/assign", json={"load_id": load_id, "auto": True})
    assert assign.status_code == 200

    sent = client.post(f"/ops/integrations/driver-app/dispatch/send/{load_id}")
    assert sent.status_code == 200
    sent_payload = sent.json()
    assert sent_payload["dispatch_id"].startswith("DSP-")
    assert sent_payload["load_id"] == load_id
    assert sent_payload["status"] == "sent"

    feed = client.get("/ops/integrations/driver-app/dispatch/feed?limit=10")
    assert feed.status_code == 200
    feed_payload = feed.json()
    assert isinstance(feed_payload["items"], list)
    assert any(item["load_id"] == load_id for item in feed_payload["items"])

    batch = client.post("/ops/integrations/driver-app/dispatch/send-batch?limit=2")
    assert batch.status_code == 200
    assert "sent" in batch.json()


def test_ticket_review_queue_billing_and_export_bridge():
    load_id = _seed_and_get_load_id()

    review = client.post(
        "/ops/tickets/review",
        json={
            "load_id": load_id,
            "ticket_number": "TKT-556677",
            "gps_miles": 104.0,
            "rated_miles": 102.0,
            "zone": "FL-Z2",
        },
    )
    assert review.status_code == 200
    review_data = review.json()
    assert review_data["review_id"].startswith("REV-")
    assert review_data["status"] in {"approved", "exception"}

    queue = client.get("/ops/tickets/queue")
    assert queue.status_code == 200
    assert len(queue.json()["items"]) >= 1

    billing = client.get("/ops/billing/readiness")
    assert billing.status_code == 200
    assert any(item["load_id"] == load_id for item in billing.json()["items"])

    export = client.post(f"/ops/integrations/mcleod/export/{load_id}")
    assert export.status_code == 200
    export_data = export.json()
    assert export_data["status"] == "generated"
    assert Path(export_data["artifact_path"]).exists()


def test_ticket_approval_completes_load_and_releases_driver():
    seeded = client.post(
        "/ops/seed/demo-pack",
        json={"seed": 41, "loads": 4, "docs_per_load": 4, "include_exceptions_ratio": 0.0, "index_documents": False},
    )
    assert seeded.status_code == 200
    load_id = seeded.json()["load_ids"][0]

    assign = client.post("/ops/dispatch/assign", json={"load_id": load_id, "auto": True})
    assert assign.status_code == 200
    driver_id = assign.json()["driver_id"]
    board_before = client.get("/ops/dispatch/board").json()
    load_before = next(row for row in board_before["loads"] if row["load_id"] == load_id)

    review = client.post(
        "/ops/tickets/review",
        json={
            "load_id": load_id,
            "ticket_number": "TKT-97095869923",
            "rated_miles": float(load_before["planned_miles"]),
            "gps_miles": float(load_before["planned_miles"]) * 1.01,
            "zone": load_before["zone"],
            "expected_rate": float(load_before["rate_total"]),
        },
    )
    assert review.status_code == 200
    assert review.json()["status"] == "approved"

    board = client.get("/ops/dispatch/board")
    assert board.status_code == 200
    load_row = next(row for row in board.json()["loads"] if row["load_id"] == load_id)
    assert load_row["status"] == "delivered"
    driver_row = next(row for row in board.json()["drivers"] if row["driver_id"] == driver_id)
    assert driver_row["status"] == "available"

    dossier = client.get(f"/ops/tickets/load/{load_id}")
    assert dossier.status_code == 200
    assert dossier.json()["load"]["status"] == "delivered"
    assert len(dossier.json()["reviews"]) >= 1


def test_exception_ticket_stays_assigned_until_resolved():
    seeded = client.post(
        "/ops/seed/demo-pack",
        json={"seed": 43, "loads": 4, "docs_per_load": 4, "include_exceptions_ratio": 0.0, "index_documents": False},
    )
    assert seeded.status_code == 200
    load_id = seeded.json()["load_ids"][1]

    assign = client.post("/ops/dispatch/assign", json={"load_id": load_id, "auto": True})
    assert assign.status_code == 200
    driver_id = assign.json()["driver_id"]

    review = client.post(
        "/ops/tickets/review",
        json={
            "load_id": load_id,
            "ticket_number": "TKT-99111222333",
            "rated_miles": 120.0,
            "gps_miles": 160.0,
            "zone": "FL-Z9",
            "expected_rate": 700.0,
        },
    )
    assert review.status_code == 200
    assert review.json()["status"] == "exception"
    review_id = review.json()["review_id"]

    board = client.get("/ops/dispatch/board").json()
    load_row = next(row for row in board["loads"] if row["load_id"] == load_id)
    assert load_row["status"] == "assigned"
    driver_row = next(row for row in board["drivers"] if row["driver_id"] == driver_id)
    assert driver_row["status"] == "assigned"

    resolve = client.post(f"/ops/tickets/{review_id}/decision", json={"decision": "resolve", "note": "cleared"})
    assert resolve.status_code == 200

    board_after = client.get("/ops/dispatch/board").json()
    load_after = next(row for row in board_after["loads"] if row["load_id"] == load_id)
    assert load_after["status"] == "delivered"
    driver_after = next(row for row in board_after["drivers"] if row["driver_id"] == driver_id)
    assert driver_after["status"] == "available"


def test_metrics_and_copilot_endpoint_available():
    _seed_and_get_load_id()

    metrics = client.get("/ops/metrics")
    assert metrics.status_code == 200
    data = metrics.json()
    assert "auto_approval_rate" in data
    assert "p95_review_latency_ms" in data

    copilot = client.post(
        "/ops/copilot/query",
        json={"query": "who is the broker for load LOAD00001?", "load_id": "LOAD00001"},
    )
    assert copilot.status_code == 200
    copilot_data = copilot.json()
    assert "answer" in copilot_data
    assert "confidence" in copilot_data


def test_copilot_free_roam_mode_falls_back_when_provider_unavailable():
    _seed_and_get_load_id()
    response = client.post(
        "/ops/copilot/query",
        json={"query": "which drivers are available", "mode": "free_roam", "session_id": "test"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "answer" in payload
    assert payload["route"] in {"deterministic", "free_roam", "free_roam_unavailable"}


def test_copilot_free_roam_mode_uses_agent_when_available(monkeypatch):
    async def _fake_query(query: str, tenant_id: str, actor: str, session_id: str = "atlas", load_id_hint: str | None = None):
        return CopilotQueryResponse(
            answer=f"free-roam handled: {query}",
            sources=[{"filename": "agent_actions", "document_type": "system_state", "similarity": 1.0}],
            confidence=0.93,
            processing_time_ms=11.0,
            route="free_roam",
            actions=[{"tool": "dispatch_summary", "ok": True}],
        )

    monkeypatch.setattr(ops_engine._free_roam_agent, "query", _fake_query)
    response = client.post(
        "/ops/copilot/query",
        json={"query": "assign all available drivers", "mode": "free_roam", "session_id": "test"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["route"] == "free_roam"
    assert payload["answer"].startswith("free-roam handled:")
    assert len(payload["actions"]) == 1


def test_idempotency_key_prevents_duplicate_mutations():
    payload = {
        "customer": "IDEMPOTENT CUSTOMER",
        "pickup_location": "Plant A",
        "delivery_location": "Jobsite B",
        "planned_miles": 22.0,
        "rate_total": 110.0,
    }
    headers = {"Idempotency-Key": "create-load-1"}
    first = client.post("/ops/dispatch/loads", json=payload, headers=headers)
    second = client.post("/ops/dispatch/loads", json=payload, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["load_id"] == second.json()["load_id"]


def test_role_enforcement_blocks_unauthorized_actor():
    load_id = _seed_and_get_load_id()
    response = client.post(
        f"/ops/integrations/mcleod/export/{load_id}",
        headers={"X-Actor-Role": "dispatcher"},
    )
    assert response.status_code == 403


def test_samsara_sync_requires_explicit_live_config():
    load_id = _seed_and_get_load_id()
    response = client.post(
        "/ops/integrations/samsara/sync",
        json={"load_ids": [load_id], "hours_back": 24},
    )
    assert response.status_code == 400
    assert "SAMSARA_EVENTS_URL" in response.json()["detail"]


def test_runtime_endpoint_exposes_mode_flags():
    response = client.get("/ops/runtime")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] in {"demo", "production"}
    assert "features" in payload
    assert "synthetic_seed_enabled" in payload["features"]


def test_samsara_sync_records_live_events(monkeypatch):
    load_id = _seed_and_get_load_id()

    def _fake_fetch(tenant_id: str, load_ids: list[str], hours_back: int):
        assert tenant_id == "demo"
        assert load_ids == [load_id]
        assert hours_back == 24
        return [
            {
                "load_id": load_id,
                "gps_miles": 101.4,
                "stop_events": 3,
                "vehicle_id": "truck-682",
                "window_start": "2026-02-16T00:00:00Z",
                "window_end": "2026-02-16T23:59:59Z",
            }
        ]

    monkeypatch.setattr(ops_engine, "_fetch_samsara_events", _fake_fetch)
    response = client.post(
        "/ops/integrations/samsara/sync",
        json={"load_ids": [load_id], "hours_back": 24},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "samsara_live"
    assert payload["synced"] == 1
    assert payload["events"][0]["load_id"] == load_id


def test_autonomy_cycle_assigns_and_reviews_new_loads():
    create = client.post(
        "/ops/dispatch/loads",
        json={
            "customer": "AUTO CYCLE CUSTOMER",
            "pickup_location": "Plant",
            "delivery_location": "Jobsite",
            "planned_miles": 44.0,
            "rate_total": 220.0,
        },
    )
    assert create.status_code == 200
    load_id = create.json()["load_id"]

    run = client.post(
        "/ops/autonomy/run",
        json={"max_loads": 25, "include_exports": False},
    )
    assert run.status_code == 200
    result = run.json()
    assert result["scanned_loads"] >= 1
    assert result["assigned_loads"] >= 1
    assert result["reviewed_loads"] >= 1

    timeline = client.get(f"/ops/loads/{load_id}/timeline")
    assert timeline.status_code == 200
    event_types = {row["event_type"] for row in timeline.json()["events"]}
    assert "load_assigned" in event_types
    assert "ticket_reviewed" in event_types


def test_load_status_transition_and_version_conflict():
    create = client.post(
        "/ops/dispatch/loads",
        json={
            "customer": "STATUS FLOW",
            "pickup_location": "Plant",
            "delivery_location": "Site",
            "planned_miles": 31.0,
            "rate_total": 155.0,
        },
    )
    assert create.status_code == 200
    load = create.json()
    load_id = load["load_id"]

    # Invalid direct transition should be rejected.
    invalid = client.post(
        f"/ops/dispatch/loads/{load_id}/status",
        json={"status": "delivered", "expected_version": load["version"]},
    )
    assert invalid.status_code == 400

    assign = client.post("/ops/dispatch/assign", json={"load_id": load_id, "auto": True})
    assert assign.status_code == 200

    board = client.get("/ops/dispatch/board")
    row = next(item for item in board.json()["loads"] if item["load_id"] == load_id)
    current_version = row["version"]

    start = client.post(
        f"/ops/dispatch/loads/{load_id}/status",
        json={"status": "en_route", "expected_version": current_version},
    )
    assert start.status_code == 200
    started = start.json()
    assert started["status"] == "en_route"

    # stale version must fail
    stale = client.post(
        f"/ops/dispatch/loads/{load_id}/status",
        json={"status": "delivered", "expected_version": current_version},
    )
    assert stale.status_code == 400


def test_adapter_ingest_powers_review_gps_miles():
    create = client.post(
        "/ops/dispatch/loads",
        json={
            "customer": "GPS SOURCE CUSTOMER",
            "pickup_location": "Plant",
            "delivery_location": "Site",
            "planned_miles": 100.0,
            "rate_total": 300.0,
        },
    )
    assert create.status_code == 200
    load_id = create.json()["load_id"]

    unauthorized = client.post(
        "/samsara-adapter/events/ingest",
        json={
            "tenant_id": "demo",
            "events": [{"load_id": load_id, "gps_miles": 103.5}],
        },
    )
    assert unauthorized.status_code == 401

    ingest = client.post(
        "/samsara-adapter/events/ingest",
        headers={"Authorization": "Bearer adapter-token"},
        json={
            "tenant_id": "demo",
            "events": [
                {
                    "load_id": load_id,
                    "gps_miles": 103.5,
                    "stop_events": 3,
                    "vehicle_id": "truck-101",
                }
            ],
        },
    )
    assert ingest.status_code == 200
    assert ingest.json()["ingested"] == 1

    review = client.post(
        "/ops/tickets/review",
        json={
            "load_id": load_id,
            "ticket_number": "TKT-445566",
            "rated_miles": 102.0,
            "zone": "FL-Z1",
        },
    )
    assert review.status_code == 200
    profile = {row["field"]: row for row in review.json()["confidence_profile"]}
    assert float(profile["gps_miles"]["value"]) == 103.5


def test_seed_blocked_when_mode_is_production():
    settings = get_settings()
    original_mode = settings.app_mode
    try:
        settings.app_mode = "production"
        response = client.post("/ops/seed/synthetic", json={"seed": 42, "loads": 2, "include_exceptions_ratio": 0.1})
        assert response.status_code == 403
    finally:
        settings.app_mode = original_mode


def test_demo_pack_seed_and_ops_state_copilot_answers():
    seeded = client.post(
        "/ops/seed/demo-pack",
        json={"seed": 21, "loads": 6, "docs_per_load": 3, "include_exceptions_ratio": 0.2},
    )
    assert seeded.status_code == 200
    payload = seeded.json()
    assert payload["loads_created"] == 6
    assert payload["documents_created"] >= 18

    drivers = client.post(
        "/ops/copilot/query",
        json={"query": "which drivers are available right now"},
    )
    assert drivers.status_code == 200
    drivers_payload = drivers.json()
    assert "available" in drivers_payload["answer"].lower()
    assert any(src.get("document_type") == "system_state" for src in drivers_payload.get("sources", []))

    load_id = payload["load_ids"][0]
    broker_invoice = client.post(
        "/ops/copilot/query",
        json={"query": f"whos the broker and whats the invoice for load {load_id}"},
    )
    assert broker_invoice.status_code == 200
    bi_payload = broker_invoice.json()
    assert load_id in bi_payload["answer"]
    assert "broker" in bi_payload["answer"].lower()
    assert "invoice" in bi_payload["answer"].lower()
    assert len(bi_payload.get("sources", [])) >= 1


def test_copilot_handles_driver_and_load_state_intents():
    seeded = client.post(
        "/ops/seed/demo-pack",
        json={"seed": 77, "loads": 8, "docs_per_load": 2, "include_exceptions_ratio": 0.0, "index_documents": False},
    )
    assert seeded.status_code == 200
    payload = seeded.json()
    load_id = payload["load_ids"][0]

    assign = client.post("/ops/dispatch/assign", json={"load_id": load_id, "auto": True})
    assert assign.status_code == 200

    who_did = client.post("/ops/copilot/query", json={"query": f"who did {load_id}"})
    assert who_did.status_code == 200
    assert load_id in who_did.json()["answer"]
    assert "assigned to" in who_did.json()["answer"].lower()

    unknown = client.post("/ops/copilot/query", json={"query": "who did LOAD99999999"})
    assert unknown.status_code == 200
    assert "not in the current dispatch board" in unknown.json()["answer"].lower()

    total = client.post("/ops/copilot/query", json={"query": "how many drivers d i have"})
    assert total.status_code == 200
    assert "drivers total" in total.json()["answer"].lower()

    roster = client.post("/ops/copilot/query", json={"query": "who are my drivers"})
    assert roster.status_code == 200
    assert "driver roster" in roster.json()["answer"].lower()


def test_copilot_ticket_flags_and_ticket_lookup():
    load_resp = client.post(
        "/ops/dispatch/loads",
        json={
            "customer": "FLAG TEST CUSTOMER",
            "pickup_location": "Fort Myers, FL",
            "delivery_location": "Tampa, FL",
            "planned_miles": 110.0,
            "rate_total": 500.0,
            "zone": "FL-Z1",
        },
    )
    assert load_resp.status_code == 200
    load_id = load_resp.json()["load_id"]

    review = client.post(
        "/ops/tickets/review",
        json={
            "load_id": load_id,
            "ticket_number": "TKT-562604873928",
            "rated_miles": 110.0,
            "gps_miles": 143.0,
            "zone": "FL-Z9",
            "expected_rate": 500.0,
        },
    )
    assert review.status_code == 200
    assert review.json()["status"] == "exception"

    flagged = client.post("/ops/copilot/query", json={"query": "what tickets got flagged?"})
    assert flagged.status_code == 200
    assert "flagged ticket" in flagged.json()["answer"].lower()
    assert "TKT-562604873928".lower() in flagged.json()["answer"].lower()

    flagged_alias = client.post("/ops/copilot/query", json={"query": "what TKT's did not pass"})
    assert flagged_alias.status_code == 200
    assert "flagged ticket" in flagged_alias.json()["answer"].lower()

    flagged_short = client.post("/ops/copilot/query", json={"query": "what TKT failed?"})
    assert flagged_short.status_code == 200
    assert "flagged ticket" in flagged_short.json()["answer"].lower()

    details = client.post("/ops/copilot/query", json={"query": "what is wrong with TKT-562604873928"})
    assert details.status_code == 200
    answer = details.json()["answer"].lower()
    assert "ticket tkt-562604873928" in answer
    assert "missing docs" in answer
    assert details.json()["confidence"] >= 0.88

    short_load = f"LOAD{int(load_id.replace('LOAD', '')):03d}"
    pass_status = client.post("/ops/copilot/query", json={"query": f"for {short_load} did the tkt pass?"})
    assert pass_status.status_code == 200
    assert "latest ticket for" in pass_status.json()["answer"].lower()


def test_copilot_driver_activity_and_load_ticket_issue_queries():
    seeded = client.post(
        "/ops/seed/demo-pack",
        json={"seed": 88, "loads": 6, "docs_per_load": 4, "include_exceptions_ratio": 0.0, "index_documents": False},
    )
    assert seeded.status_code == 200
    load_id = seeded.json()["load_ids"][0]

    assign = client.post("/ops/dispatch/assign", json={"load_id": load_id, "auto": True})
    assert assign.status_code == 200

    board = client.get("/ops/dispatch/board").json()
    load_row = next(row for row in board["loads"] if row["load_id"] == load_id)
    driver_name = assign.json()["driver_name"]

    approved = client.post(
        "/ops/tickets/review",
        json={
            "load_id": load_id,
            "ticket_number": "TKT-33445566778",
            "rated_miles": float(load_row["planned_miles"]),
            "gps_miles": float(load_row["planned_miles"]) * 1.01,
            "zone": load_row["zone"],
            "expected_rate": float(load_row["rate_total"]),
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    loads_q = client.post("/ops/copilot/query", json={"query": f"what loads did {driver_name} do"})
    assert loads_q.status_code == 200
    assert "load(s)" in loads_q.json()["answer"].lower()

    miles_q = client.post("/ops/copilot/query", json={"query": f"how many miles did {driver_name} drive"})
    assert miles_q.status_code == 200
    assert "miles" in miles_q.json()["answer"].lower()

    issue_q = client.post("/ops/copilot/query", json={"query": f"what is wrong with {load_id} ticket"})
    assert issue_q.status_code == 200
    assert "no blocking issue is open" in issue_q.json()["answer"].lower()

    run_review = client.post("/ops/copilot/query", json={"query": "run ticet review for me"})
    assert run_review.status_code == 200
    assert "reviewed" in run_review.json()["answer"].lower()


def test_copilot_auto_assign_returns_assignment_ticket_links_and_summary():
    seeded = client.post(
        "/ops/seed/demo-pack",
        json={"seed": 118, "loads": 8, "docs_per_load": 4, "include_exceptions_ratio": 0.0, "index_documents": False},
    )
    assert seeded.status_code == 200

    response = client.post(
        "/ops/copilot/query",
        json={"query": "Assign the 2 available drivers to new loads and tell me which loads you did"},
    )
    assert response.status_code == 200
    answer = response.json()["answer"].lower()
    assert "assignments:" in answer
    assert "ticket checks:" in answer
    assert "summary ->" in answer


def test_copilot_route_miles_stops_and_multiload_invoice_queries():
    seeded = client.post(
        "/ops/seed/demo-pack",
        json={"seed": 211, "loads": 6, "docs_per_load": 4, "include_exceptions_ratio": 0.0, "index_documents": False},
    )
    assert seeded.status_code == 200
    load_ids = seeded.json()["load_ids"]
    load_a = load_ids[0]
    load_b = load_ids[1]

    assign = client.post("/ops/dispatch/assign", json={"load_id": load_a, "auto": True})
    assert assign.status_code == 200
    board = client.get("/ops/dispatch/board").json()
    driver_name = assign.json()["driver_name"]

    samsara = client.post(
        "/samsara-adapter/events/ingest",
        headers={"Authorization": "Bearer adapter-token"},
        json={
            "tenant_id": "demo",
            "events": [
                {"load_id": load_a, "gps_miles": 121.4, "stop_events": 3, "vehicle_id": "truck-1"},
            ],
        },
    )
    assert samsara.status_code == 200

    route_q = client.post(
        "/ops/copilot/query",
        json={"query": f"what is the location of pickup and dropoff based on driver route for {load_a}"},
    )
    assert route_q.status_code == 200
    route_answer = route_q.json()["answer"].lower()
    assert "pickup" in route_answer and "dropoff" in route_answer

    miles_q = client.post("/ops/copilot/query", json={"query": f"what are the miles for {load_a}"})
    assert miles_q.status_code == 200
    assert "planned miles" in miles_q.json()["answer"].lower()

    stops_q = client.post("/ops/copilot/query", json={"query": f"how many stops did {driver_name} make"})
    assert stops_q.status_code == 200
    assert "stop" in stops_q.json()["answer"].lower()

    typo_q = client.post("/ops/copilot/query", json={"query": f"how many laods did {driver_name} do and which laods"})
    assert typo_q.status_code == 200
    assert "load(s)" in typo_q.json()["answer"].lower()

    multi_q = client.post(
        "/ops/copilot/query",
        json={"query": f"what is {load_a} broker and what is the invocie for {load_b}"},
    )
    assert multi_q.status_code == 200
    multi_answer = multi_q.json()["answer"].lower()
    assert str(load_a).lower() in multi_answer
    assert str(load_b).lower() in multi_answer
    assert "invoice" in multi_answer
