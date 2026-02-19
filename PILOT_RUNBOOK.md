# Pilot Runbook: Friend's Trucking Business

Use this to run a 2-4 week paid pilot and turn it into a case study.

## 1) Data You Need Before Build

- 30 rate confirmations (PDF)
- 30 completed invoice packets (invoice + POD/BOL + lumper when applicable)
- Average minutes to invoice one load today
- Approximate invoice kickback rate today
- Monthly invoice volume
- Top 5 kickback reasons from last 60 days

## 2) Setup

```bash
cd openclaw/backend
pip install -r requirements.txt
python scripts/import_documents.py ../sample_data/documents --limit 30
python -m uvicorn app.main:app --reload
```

In another terminal:

```bash
cd openclaw/frontend
python -m http.server 3000
```

Open `http://localhost:3000`.

Optional auth hardening for real pilot:
- set `AUTH_ENABLED=true`
- set `TENANT_TOKENS=pilot_token:friend_trucking`
- send `Authorization: Bearer pilot_token` from your UI/client

Optional Microsoft Graph ingestion:
- fill `MS_GRAPH_*` vars in `.env`
- use `/integrations/microsoft/outlook/import` and `/integrations/microsoft/teams/import`

## 3) Pilot Workflow

1. Upload or bulk-import documents from your friend’s Teams/email export.
2. For each target load, run `Assemble Invoice Packet`.
3. Confirm:
   - required docs present
   - no consistency errors
   - clear next actions
4. Use the queue view to prioritize `ready` loads for invoicing.

## 4) ROI Measurement

Set baseline in UI:
- manual minutes/invoice
- monthly invoice volume
- kickback rate
- rework minutes/kickback
- labor rate

Track these outputs weekly:
- observed automated minutes
- missing-doc rate
- monthly labor savings estimate
- monthly rework savings estimate

## 5) Go/No-Go Criteria

Minimum targets for sellable case study:
- at least 40% reduction in invoicing time
- at least 25% reduction in kickback-related rework
- stable packet completeness on repeated loads

## 6) Packaging for Next Customer

After pilot:
- capture before/after metrics with exact dates
- save 3 anonymized example loads (before vs after packet workflow)
- define pricing as fixed monthly fee vs measured monthly value
