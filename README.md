# SHAMS Autonomous Ops Platform

Sellable MVP for trucking carriers: replace clunky dispatch tooling with an AI-native system that creates/assigns loads, auto-reviews tickets, prepares billing exports, and provides cited document chat.

## What It Does Now

- Dispatch core:
  - create/update loads
  - autonomous or manual assignment
  - timeline per load
- Ticket review engine:
  - strict field/rule checks
  - autonomous approve on confidence + hard-rule pass
  - exception queue for manual decisions
- Billing + McLeod bridge:
  - billing readiness per load
  - leakage findings (zone/rate/mile mismatch)
  - export artifacts + replay ledger
- Autonomous operations:
  - one-click autonomous cycle (`/ops/autonomy/run`) to assign, review, and export ready loads
- Copilot:
  - cited document search/chat (`/ops/copilot/query`)
  - load-aware context injection
- Integrations:
  - Microsoft Graph imports
  - Samsara read-only sync (strict live mode via configured adapter endpoint)
- Ingestion:
  - PDF/TXT/EML/PNG/JPG + HEIC/HEIF normalization
  - OCR processing when `pytesseract` + Tesseract runtime are installed

## Architecture

- `backend/app/services/ops_state.py`: persistent state for loads/reviews/billing/timeline/exports
- `backend/app/services/ops_engine.py`: autonomous dispatch/ticketing/billing orchestration
- `backend/app/services/vector_store.py`: file-backed vector index (`data/vector_index.jsonl`) with a columnar metadata index + normalized cosine kernel
- `backend/app/routers/ops.py`: full ops API surface
- `backend/app/services/document_registry.py`: load-linked document registry
- `backend/app/services/extraction.py`: structured doc extraction
- `frontend/index.html`: multi-module SHAMS dashboard

## Quick Start

### 1) Configure env

```bash
cd openclaw
cp .env.example .env
```

Set at least:

```bash
# Local Ollama mode (no paid API key):
OPENAI_API_KEY=
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:3b-instruct
EMBEDDING_MODEL=nomic-embed-text:latest
VECTOR_INDEX_PATH=./data/vector_index.jsonl

# Or OpenAI cloud:
# OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=https://api.openai.com/v1
# Optional multi-tenant auth
AUTH_ENABLED=false
DEFAULT_TENANT_ID=demo
TENANT_TOKENS=
```

At least one generation/embedding path must be configured:
- `TINKER_MODEL_PATH=...` for local Tinker inference
- or `OPENAI_BASE_URL` + `OPENAI_API_KEY` (OpenAI-compatible endpoint, including local providers)

`TENANT_TOKENS` format:
```bash
TENANT_TOKENS=tokenA:friend_trucking,tokenB:demo2
```

If `AUTH_ENABLED=true`, send:
- `Authorization: Bearer <token>`
- optional `X-Tenant-ID` (must match token tenant)
- optional `X-Actor-Role` (`dispatcher`, `billing`, `admin`) for role-scoped APIs

`APP_MODE` controls demo-only features:
- `APP_MODE=demo`: synthetic seed + demo flow enabled
- `APP_MODE=production`: synthetic seed disabled

Mutation endpoints support `Idempotency-Key` for safe retries without duplicate operations.
SQLite ops state defaults to `<OPS_STATE_PATH>.db` unless `OPS_DB_PATH` is explicitly set.
Load mutations support optimistic concurrency via `expected_version` in update/status payloads.

For image OCR (`.jpg/.png/.heic`), install Tesseract binary on host (plus `pytesseract` Python package from requirements).

Samsara sync is intentionally strict (no simulated fallback):
- set `SAMSARA_API_TOKEN`
- set `SAMSARA_EVENTS_URL` to your telemetry adapter that returns:
  - `{"events":[{"load_id":"LOAD00001","gps_miles":101.2,"stop_events":3,"vehicle_id":"...","window_start":"...","window_end":"..."}]}`
- built-in adapter endpoint is available at `POST /samsara-adapter/events/query`

Example with built-in adapter:
```bash
# .env
SAMSARA_API_TOKEN=adapter-token
SAMSARA_EVENTS_URL=http://localhost:8000/samsara-adapter/events/query
```

Ingest telemetry:
```bash
curl -X POST http://localhost:8000/samsara-adapter/events/ingest \
  -H "Authorization: Bearer adapter-token" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id":"demo",
    "events":[{"load_id":"LOAD01000","gps_miles":103.5,"stop_events":3,"vehicle_id":"truck-682"}]
  }'
```

### 2) Run backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

### 3) Open frontend

```bash
cd ../frontend
python -m http.server 3000
```

Open `http://localhost:3000`.

## Bulk Import for Pilot Data

Point the importer at your friend’s exported folder:

```bash
cd openclaw/backend
python scripts/import_documents.py ../sample_data/documents
```

Limit import count during testing:

```bash
python scripts/import_documents.py ../sample_data/documents --limit 30
```

## Core API Endpoints

### Documents
- `POST /documents/upload`
- `GET /documents`
- `GET /documents/{document_id}`
- `POST /documents/extract`
- `GET /documents/stats`
- `DELETE /documents/{document_id}`

### Invoice Packet Workflow
- `POST /workflows/invoice-packet`
- `GET /workflows/invoice-packet/metrics`
- `GET /workflows/invoice-packet/queue`
- `POST /workflows/invoice-packet/baseline`
- `GET /workflows/invoice-packet/roi`

### SHAMS Ops
- `GET /ops/dispatch/board`
- `POST /ops/dispatch/loads`
- `PATCH /ops/dispatch/loads/{load_id}`
- `POST /ops/dispatch/loads/{load_id}/status`
- `POST /ops/dispatch/assign`
- `POST /ops/tickets/review`
- `GET /ops/tickets/queue`
- `POST /ops/tickets/{review_id}/decision`
- `GET /ops/billing/readiness`
- `POST /ops/integrations/mcleod/export/{load_id}`
- `GET /ops/integrations/mcleod/ledger`
- `POST /ops/integrations/mcleod/replay/{export_id}`
- `POST /ops/integrations/samsara/sync`
- `GET /ops/loads/{load_id}/timeline`
- `POST /ops/copilot/query`
- `POST /ops/seed/synthetic`
- `POST /ops/seed/demo-pack`
- `GET /ops/metrics`
- `POST /ops/autonomy/run`
- `GET /ops/runtime`
- `POST /samsara-adapter/events/ingest`
- `POST /samsara-adapter/events/query`

### Runtime + health
- `POST /rag/query`
- `GET /rag/health`
- `GET /rag/metrics`
- `GET /health`

## Demo It Fast

1) Run backend + frontend:

```bash
cd openclaw/backend
python -m uvicorn app.main:app --reload

# new terminal
cd openclaw/frontend
python -m http.server 3000
```

2) Open:
- UI: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`

3) Verify runtime is using your fine-tuned checkpoint:

```bash
curl http://localhost:8000/rag/health
```

Look for:
- `runtime.provider = "tinker"`
- `runtime.model = tinker://.../sampler_weights/...`
- low-latency caps (`latency_budget_seconds`, `max_context_chunks`, `max_answer_tokens`)

You can inspect rolling p95 latency and route counts:

```bash
curl http://localhost:8000/rag/metrics
```

4) Smoke test AP query path (fast deterministic answer):

```bash
curl -X POST http://localhost:8000/rag/query \
  -H "Content-Type: application/json" \
  -d '{"query":"whos the broker and whats the invoice for load LOAD00030"}'
```

5) Run full workflow demo script:

```bash
cd openclaw
python3 test_workflows.py
```

For AP facts questions (broker/invoice/rate), include a load ID (`LOADxxxxx`) so the copilot can return exact values with citations in milliseconds.

To preload a full presentation dataset (loads + tickets + billing + synthetic docs with citations):

```bash
curl -X POST http://localhost:8000/ops/seed/demo-pack \
  -H "Content-Type: application/json" \
  -d '{"seed":42,"loads":24,"docs_per_load":3,"include_exceptions_ratio":0.24}'
```

### Integrations
- `GET /integrations/microsoft/status`
- `POST /integrations/microsoft/outlook/import`
- `POST /integrations/microsoft/teams/import`

Example Outlook import:
```bash
curl -X POST http://localhost:8000/integrations/microsoft/outlook/import \
  -H "Content-Type: application/json" \
  -d '{"folder":"inbox","days_back":7,"max_messages":30}'
```

Example Teams/Drive import:
```bash
curl -X POST http://localhost:8000/integrations/microsoft/teams/import \
  -H "Content-Type: application/json" \
  -d '{"root_path":"General","recursive":true,"max_files":100}'
```

## Pilot Data You Need from Your Friend

- 30 rate confirmations (PDF)
- 30 completed invoice packets (invoice + POD/BOL + lumper if any)
- Current average minutes to invoice one load
- Approximate invoice kickback rate
- Monthly invoice volume
- Top kickback reasons from recent loads

## How to Demonstrate ROI

1. Set baseline via API (`POST /workflows/invoice-packet/baseline`)
2. Assemble packets on real loads
3. Show:
   - observed automated minutes
   - missing-doc rate
   - monthly labor savings
   - monthly rework savings
4. Use output to price monthly subscription vs value created

## Optional: Small LoRA Fine-Tune (Tinker)

Build a small supervised dataset from invoice/rate-con PDFs:

```bash
cd openclaw/backend
python scripts/build_sft_dataset.py \
  --docs-root ../sample_data/documents \
  --output-dir ./data/finetune
```

Train LoRA on Tinker:

```bash
export TINKER_API_KEY=tml-...
python scripts/tinker_train_lora.py \
  --train-file ./data/finetune/train.jsonl \
  --base-model meta-llama/Llama-3.2-1B \
  --steps 40 \
  --batch-size 4 \
  --rank 8 \
  --save-name shams_trucking_l32_1b
```

Run inference on the fine-tuned checkpoint directly in Tinker:

```bash
export TINKER_API_KEY=tml-...
python scripts/tinker_sample.py \
  --model-path tinker://<model_id>/sampler_weights/<checkpoint_name> \
  --prompt "Question: For load LOAD00030, who's the broker and what's the invoice number? Answer:"
```

Note: Tinker-generated PEFT adapters may fail to import into Ollama directly (`adapter_config.json` lookup issue). Keep local serving on base Ollama models and use SHAMS deterministic load/BOL fact paths for reliable, low-latency answers.

Latency tuning knobs for production:
- `RAG_MAX_CONTEXT_CHUNKS`
- `RAG_CHUNK_CHAR_LIMIT`
- `RAG_CONTEXT_CHAR_LIMIT`
- `RAG_GENERATION_TIMEOUT_SECONDS`
- `RAG_ANSWER_MAX_TOKENS`
- `RAG_CACHE_TTL_SECONDS`
- `RAG_METRICS_WINDOW_SIZE`

Run a repeatable latency benchmark:

```bash
cd openclaw/backend
python scripts/benchmark_rag_latency.py \
  --base-url http://localhost:8000 \
  --iterations 5 \
  --target-ms 3000 \
  --output ./data/benchmarks/rag_latency.json
```

For uncached latency profiling, add `--cache-bust`.

Run vector-kernel perf benchmark (retrieval core):

```bash
cd openclaw/backend
python scripts/benchmark_vector_kernel.py \
  --chunks 15000 \
  --dim 512 \
  --queries 80 \
  --target-p95-ms 12 \
  --output ./data/benchmarks/vector_kernel.json
```

## Test Gates

Run full backend test coverage (API + state + perf tooling):

```bash
cd openclaw
python3 -m pytest -q backend/tests
```
