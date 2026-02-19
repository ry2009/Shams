# SHAMS Ops

Autonomous dispatch + ticketing + billing operations for trucking companies.

SHAMS replaces repetitive back-office workflows with an AI control layer that is fast, auditable, and deployable on top of existing systems.

## Investor TL;DR

- Trucking back offices still run on manual clicks, spreadsheets, email attachments, and legacy TMS interfaces.
- SHAMS automates the highest-frequency workflows: load assignment, ticket review, billing readiness, export, and follow-up actions.
- The platform combines deterministic operations logic with a free-roam agent that can execute real actions, not just answer questions.
- Every action is logged with timeline events, confidence, and review state, so operators can trust and audit outputs.
- SHAMS is designed as a bridge product first (works with legacy tools now), then migration layer later (becomes the operating system).

## The Problem

Most carriers lose margin in operational friction:

- too many manual steps to assign and manage loads
- ticket/document review delays that block billing
- missing docs and rate/mileage mismatches causing rework
- fragmented systems that force staff to context-switch all day

This is not a “better dashboard” problem. It is an execution-automation problem.

## What SHAMS Does

### 1) Dispatch Center

- create and update loads
- auto-assign drivers based on live state
- send dispatch packets to driver app channel
- track dispatch feed and assignment outcomes

### 2) Ticket Review Engine

- run validation checks on submitted ticket context
- auto-approve clean tickets
- flag exceptions with explicit reasons and failed rules
- hold unresolved loads until corrected/resolved

### 3) Billing Ledger + Legacy Bridge

- compute billing readiness per load
- surface blockers and missing requirements
- export billing artifacts via McLeod bridge
- maintain replayable export ledger for audit

### 4) Atlas Agent Layer

- free-roam operations agent for natural-language execution
- deterministic tool layer for predictable critical workflows
- action traces displayed in UI for transparency
- answers across docs + live system state in one interface

### 5) Agent OS (Admin)

- objective-driven autonomous runs
- autonomy levels, execution modes, and run controls
- pending approvals and run timeline inspection

## Why This Product Wins

- **Execution over chat:** SHAMS does work (assign, review, resolve, export), not only Q&A.
- **Deterministic + agent hybrid:** stable ops primitives with flexible language control.
- **Auditability by design:** event timelines, status transitions, ticket decision history.
- **Legacy-compatible wedge:** immediate value without forcing day-one rip-and-replace.
- **Clear ROI path:** measure time saved, auto-approval %, billing-ready lift, leakage recovered.

## Live Today in This Repo

- full ops API for dispatch/tickets/billing/copilot
- free-roam + deterministic copilot execution routing
- synthetic demo seeding for realistic investor demos
- driver-app dispatch send and dispatch feed
- ticket dossier modal and exception resolution flow
- backend test suite covering API, state, and performance tooling

## Business Value SHAMS Targets

- fewer manual ops hours per load
- faster invoice readiness and cash conversion
- lower exception/rework rate
- reduced leakage from preventable mismatches
- higher throughput per dispatcher/accounting headcount

## ICP and GTM (Current)

- **Initial wedge:** small-to-mid carriers with high manual ops load
- **Buyer profiles:** operations manager, dispatch lead, billing/accounting lead, owner-operator
- **Land motion:** deploy on a pilot lane/team with synthetic + real docs
- **Expand motion:** move from dispatch/tickets into full back-office orchestration

## Product Vision

SHAMS becomes the operations control plane for freight:

- natural-language command surface for non-technical teams
- autonomous execution for repetitive workflows
- human review where risk requires it
- full traceability for compliance and customer trust

In short: one system where back-office work is orchestrated by agents and supervised by operators.

## Quick Start (3 Minutes)

```bash
cp .env.example .env
```

Run backend:

```bash
cd backend
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Run frontend:

```bash
cd ../frontend
python3 -m http.server 3000
```

Open:

- UI: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

## Investor Demo Script (Fast)

1. Click `Reset Demo Data`.
2. Click `Run Guided Demo`.
3. Open Atlas and run:
   - `Assign available drivers to planned loads and run ticket checks.`
   - `What tickets are flagged? Resolve the first one.`
   - `Summarize what changed and what still needs human attention.`
4. In Dispatch Center:
   - send dispatch packets (`Dispatch` or `Send Batch`)
   - show live Driver App Dispatch Feed updates
5. In Ticket Review + Billing:
   - show approved vs exception path
   - show billing readiness and export actions

This demonstrates end-to-end autonomous operations, not just chat responses.

## Core API Groups

- `GET /ops/dispatch/board`
- `POST /ops/dispatch/loads`
- `POST /ops/dispatch/assign`
- `POST /ops/tickets/review`
- `GET /ops/tickets/queue`
- `POST /ops/tickets/{review_id}/decision`
- `GET /ops/billing/readiness`
- `POST /ops/integrations/mcleod/export/{load_id}`
- `POST /ops/integrations/driver-app/dispatch/send/{load_id}`
- `POST /ops/integrations/driver-app/dispatch/send-batch`
- `GET /ops/integrations/driver-app/dispatch/feed`
- `POST /ops/copilot/query`
- `POST /ops/seed/demo-pack`
- `GET /ops/runtime`
- `GET /ops/metrics`

## Repo Map

- `backend/app/services/ops_engine.py` - business orchestration
- `backend/app/services/free_roam_agent.py` - free-roam tool-calling agent
- `backend/app/services/ops_state.py` - persistent ops state and ledgers
- `backend/app/routers/ops.py` - ops API surface
- `frontend/index.html` - SHAMS product UI and Atlas experience

## Tests

```bash
cd backend
pytest -q
```

