"""API tests for SHAMS Agent OS orchestration layer."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


TMP = Path(__file__).resolve().parent / ".tmp_agent_os"
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


client = TestClient(app)


def _seed_ops() -> None:
    response = client.post("/ops/seed/synthetic", json={"seed": 22, "loads": 6, "include_exceptions_ratio": 0.2})
    assert response.status_code == 200


def test_agent_os_dispatch_run_completes_and_records_steps():
    _seed_ops()
    run = client.post(
        "/agent-os/runs",
        json={
            "objective": "Assign available drivers to planned loads",
            "autonomy_level": "L3",
            "execution_mode": "hybrid",
        },
    )
    assert run.status_code == 200
    payload = run.json()
    assert payload["run"]["status"] in {"completed", "completed_with_warnings"}
    assert len(payload["steps"]) >= 1
    assert any(step["action_type"] == "dispatch.assign_loads" for step in payload["steps"])

    lookup = client.get(f"/agent-os/runs/{payload['run']['run_id']}")
    assert lookup.status_code == 200
    assert lookup.json()["run"]["run_id"] == payload["run"]["run_id"]


def test_agent_os_destructive_action_requires_approval_then_executes():
    _seed_ops()
    run = client.post(
        "/agent-os/runs",
        json={"objective": "wipe reset all demo data", "autonomy_level": "L3", "execution_mode": "hybrid"},
    )
    assert run.status_code == 200
    payload = run.json()
    assert payload["run"]["status"] == "waiting_approval"
    assert payload["run"]["blocked_approval_id"]

    pending = client.get("/agent-os/approvals/pending")
    assert pending.status_code == 200
    pending_items = pending.json()["items"]
    assert any(item["approval_id"] == payload["run"]["blocked_approval_id"] for item in pending_items)

    resume = client.post(
        f"/agent-os/runs/{payload['run']['run_id']}/approve",
        json={
            "approval_id": payload["run"]["blocked_approval_id"],
            "approve": True,
            "note": "approved for test",
        },
    )
    assert resume.status_code == 200
    resumed_payload = resume.json()
    assert resumed_payload["run"]["status"] in {"completed", "completed_with_warnings"}
    assert any(item["status"] == "approved" for item in resumed_payload["approvals"])

    board = client.get("/ops/dispatch/board")
    assert board.status_code == 200
    assert len(board.json()["loads"]) == 0


def test_agent_os_policy_patch_can_gate_dispatch_action():
    policies = client.get("/agent-os/policies")
    assert policies.status_code == 200
    target = next(row for row in policies.json()["items"] if row["action_type"] == "dispatch.assign_loads")
    policy_id = target["policy_id"]

    patch = client.patch(
        f"/agent-os/policies/{policy_id}",
        json={"requires_admin_approval": True, "notes": "test gate"},
    )
    assert patch.status_code == 200
    assert patch.json()["requires_admin_approval"] is True

    _seed_ops()
    run = client.post(
        "/agent-os/runs",
        json={"objective": "assign loads now", "autonomy_level": "L3", "execution_mode": "hybrid"},
    )
    assert run.status_code == 200
    assert run.json()["run"]["status"] == "waiting_approval"

    restore = client.patch(
        f"/agent-os/policies/{policy_id}",
        json={"requires_admin_approval": False, "notes": "restored"},
    )
    assert restore.status_code == 200
    assert restore.json()["requires_admin_approval"] is False


def test_agent_os_add_driver_objective_creates_driver():
    board_before = client.get("/ops/dispatch/board")
    assert board_before.status_code == 200
    before_names = {row["name"] for row in board_before.json()["drivers"]}

    run = client.post(
        "/agent-os/runs",
        json={
            "objective": "add a new driver to the team named Ale Eddie",
            "autonomy_level": "L3",
            "execution_mode": "hybrid",
        },
    )
    assert run.status_code == 200
    payload = run.json()
    assert payload["run"]["status"] in {"completed", "completed_with_warnings"}
    assert any(step["action_type"] == "fleet.add_driver" for step in payload["steps"])

    board_after = client.get("/ops/dispatch/board")
    assert board_after.status_code == 200
    after_names = {row["name"] for row in board_after.json()["drivers"]}
    assert "Ale Eddie" in after_names
    expected_delta = 0 if "Ale Eddie" in before_names else 1
    assert len(after_names) == len(before_names) + expected_delta


def test_agent_os_remove_driver_requires_approval_and_removes_driver():
    create = client.post(
        "/agent-os/runs",
        json={
            "objective": "add a new driver to the team named Demo Remove",
            "autonomy_level": "L3",
            "execution_mode": "hybrid",
        },
    )
    assert create.status_code == 200

    remove = client.post(
        "/agent-os/runs",
        json={
            "objective": "delete driver Demo Remove",
            "autonomy_level": "L3",
            "execution_mode": "hybrid",
        },
    )
    assert remove.status_code == 200
    payload = remove.json()
    assert payload["run"]["status"] == "waiting_approval"
    approval_id = payload["run"]["blocked_approval_id"]
    assert approval_id

    resume = client.post(
        f"/agent-os/runs/{payload['run']['run_id']}/approve",
        json={
            "approval_id": approval_id,
            "approve": True,
            "note": "approved remove driver",
        },
    )
    assert resume.status_code == 200
    resumed = resume.json()
    assert resumed["run"]["status"] in {"completed", "completed_with_warnings"}
    assert any(step["action_type"] == "fleet.remove_driver" for step in resumed["steps"])

    board = client.get("/ops/dispatch/board")
    assert board.status_code == 200
    names = {row["name"] for row in board.json()["drivers"]}
    assert "Demo Remove" not in names
