"""Tests for vector-store kernel correctness and instrumentation."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


TMP = Path(__file__).resolve().parent / ".tmp_vector"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["VECTOR_INDEX_PATH"] = str(TMP / "vector_index.jsonl")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.models.document import Document, DocumentType  # noqa: E402
from app.services.vector_store import VectorStore  # noqa: E402


def _store() -> VectorStore:
    get_settings.cache_clear()
    path = Path(os.environ["VECTOR_INDEX_PATH"])
    if path.exists():
        path.unlink()
    return VectorStore()


def test_vector_kernel_ranks_expected_best_match():
    store = _store()
    doc = Document(id="doc1", filename="a.pdf", document_type=DocumentType.BOL, raw_text="alpha")
    chunks = [("alpha", {"chunk_index": 0}), ("beta", {"chunk_index": 1}), ("gamma", {"chunk_index": 2})]
    embeddings = [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    asyncio.run(store.add_document_chunks(doc, chunks, embeddings, tenant_id="demo"))

    matches = asyncio.run(store.search([0.91, 0.08, 0.01], top_k=2, tenant_id="demo"))
    assert len(matches) == 2
    assert matches[0]["text"] == "alpha"
    assert matches[0]["similarity"] >= matches[1]["similarity"]


def test_vector_kernel_metrics_populate_after_queries():
    store = _store()
    doc = Document(id="doc2", filename="b.pdf", document_type=DocumentType.INVOICE, raw_text="payload")
    chunks = [(f"chunk-{i}", {"chunk_index": i}) for i in range(40)]
    embeddings = [[float(i % 3 == 0), float(i % 3 == 1), float(i % 3 == 2)] for i in range(40)]
    asyncio.run(store.add_document_chunks(doc, chunks, embeddings, tenant_id="demo"))

    for _ in range(30):
        asyncio.run(store.search([1.0, 0.0, 0.0], top_k=5, tenant_id="demo"))

    stats = store.get_stats("demo")
    kernel = stats["kernel"]
    assert kernel["type"] == "numpy_cosine_kernel"
    assert kernel["embedding_dim"] == 3
    assert kernel["samples"] >= 30
    assert kernel["p95_ms"] >= 0.0
    assert kernel["metadata_columns"] >= 1


def test_vector_kernel_applies_tenant_type_and_metadata_filters():
    store = _store()

    rate_doc = Document(
        id="doc-rate",
        filename="rate.pdf",
        document_type=DocumentType.RATE_CONFIRMATION,
        raw_text="rate",
        extracted_data={"broker_name": "TQL"},
    )
    invoice_doc = Document(
        id="doc-invoice",
        filename="invoice.pdf",
        document_type=DocumentType.INVOICE,
        raw_text="invoice",
        extracted_data={"broker_name": "Coyote"},
    )

    asyncio.run(
        store.add_document_chunks(
            rate_doc,
            [("rate chunk", {"chunk_index": 0})],
            [[1.0, 0.0, 0.0]],
            tenant_id="demo",
        )
    )
    asyncio.run(
        store.add_document_chunks(
            invoice_doc,
            [("invoice chunk", {"chunk_index": 0})],
            [[0.99, 0.01, 0.0]],
            tenant_id="other",
        )
    )

    demo_rate = asyncio.run(
        store.search(
            [1.0, 0.0, 0.0],
            top_k=3,
            tenant_id="demo",
            document_types=[DocumentType.RATE_CONFIRMATION],
            filters={"extracted_broker_name": "TQL"},
        )
    )
    assert len(demo_rate) == 1
    assert demo_rate[0]["metadata"]["filename"] == "rate.pdf"

    # Tenant + metadata filter should exclude non-matching row even if embedding is similar.
    no_cross_tenant = asyncio.run(
        store.search(
            [1.0, 0.0, 0.0],
            top_k=3,
            tenant_id="demo",
            filters={"extracted_broker_name": "Coyote"},
        )
    )
    assert no_cross_tenant == []
