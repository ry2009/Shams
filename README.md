# SHAMS Ops

AI-native dispatch, ticket review, billing, and agent automation for trucking operations teams.

SHAMS is built to replace click-heavy back-office workflows with fast, auditable automation while keeping operators in control.

## What SHAMS Provides to Businesses

- Reduce manual dispatch/admin time by automating repetitive load assignment and ticket checks.
- Increase billing throughput by auto-validating load documents and surfacing exceptions early.
- Reduce revenue leakage by flagging rate/zone/mileage mismatches before export.
- Improve operational visibility with one place for loads, drivers, ticket status, billing readiness, and agent actions.
- Offer a modern control layer on top of legacy systems (including McLeod bridge workflows).

## Core Product Modules

- **Dispatch Center**
  - Create/update loads
  - Manual or auto assignment
  - Driver fleet status
  - Driver-app dispatch send + live dispatch feed

- **Ticket Review**
  - Rule-based ticket validation with confidence scoring
  - Auto-approve when checks pass
  - Exception queue with reasons and missing-doc guidance
  - Ticket dossier modal for load-level review and resolution

- **Billing Ledger**
  - Billing readiness per load
  - McLeod export artifacts + replay ledger
  - Visibility into blocked loads and missing requirements

- **Atlas Copilot**
  - Free-roam agent actions for ops tasks
  - Deterministic ops tools for assignment/review/export workflows
  - Cited answers from docs + current system state
  - Action trace shown in chat for demo transparency

- **Agent OS (Admin)**
  - Objective-driven autonomous runs
  - Run timelines, pending approvals, metrics
  - Controlled autonomy levels and execution modes

## Business KPIs SHAMS Tracks

- Active loads
- Auto-assignment rate
- Auto-approval rate
- Exception rate
- Billing-ready rate
- Estimated leakage recovered
- P95 review/response latency

## Architecture (High Level)

- `backend/app/services/ops_engine.py` - orchestration for dispatch/tickets/billing/copilot flows
- `backend/app/services/free_roam_agent.py` - tool-calling free-roam agent layer
- `backend/app/services/ops_state.py` - SQLite-backed operational state + timeline + ledgers
- `backend/app/routers/ops.py` - ops API surface
- `backend/app/services/document_registry.py` - document metadata + load linkage
- `backend/app/services/vector_store.py` - retrieval index for cited copilot responses
- `frontend/index.html` - single-page SHAMS dashboard + Atlas chat + demo controls

## Quick Start

### 1) Configure environment

```bash
cp .env.example .env
```

Minimum recommended for local demo:

```bash
APP_MODE=demo
AUTH_ENABLED=false
DEFAULT_TENANT_ID=demo
```

For free-roam Atlas with OpenRouter/OpenAI-compatible API:

```bash
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=z-ai/glm-4.5-air:free
```

### 2) Run backend

```bash
cd backend
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3) Run frontend

```bash
cd ../frontend
python3 -m http.server 3000
```

Open:

- UI: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

## Demo Flow (Customer-Ready)

1. Open SHAMS UI.
2. Click `Reset Demo Data`.
3. Click `Run Guided Demo`.
4. Open Atlas and run:
   - `Assign available drivers to planned loads and run ticket checks.`
   - `What tickets are flagged? Resolve the first one.`
   - `Summarize what changed and what still needs human attention.`
5. In Dispatch Center, use:
   - `Dispatch` on a load, or
   - `Send Batch` in Driver App Dispatch Feed
6. Show Ticket Review and Billing Ledger updates in real time.

## Key API Groups

- **Dispatch**
  - `GET /ops/dispatch/board`
  - `POST /ops/dispatch/loads`
  - `PATCH /ops/dispatch/loads/{load_id}`
  - `POST /ops/dispatch/assign`

- **Driver App Dispatch Feed**
  - `POST /ops/integrations/driver-app/dispatch/send/{load_id}`
  - `POST /ops/integrations/driver-app/dispatch/send-batch`
  - `GET /ops/integrations/driver-app/dispatch/feed`

- **Tickets + Billing**
  - `POST /ops/tickets/review`
  - `GET /ops/tickets/queue`
  - `POST /ops/tickets/{review_id}/decision`
  - `GET /ops/billing/readiness`
  - `POST /ops/integrations/mcleod/export/{load_id}`

- **Copilot + Runtime**
  - `POST /ops/copilot/query`
  - `GET /ops/runtime`
  - `GET /ops/metrics`

- **Synthetic Demo Data**
  - `POST /ops/seed/demo-pack`

## Production Intent

SHAMS is designed to become the operations control plane for carriers:

- deterministic automation for repeatable workflows
- free-roam agent orchestration for natural-language ops control
- auditable actions, role constraints, and timeline trails
- integration-first approach for legacy systems during migration

## Tests

```bash
cd backend
pytest -q
```

