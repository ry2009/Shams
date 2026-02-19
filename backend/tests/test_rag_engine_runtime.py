"""Runtime-focused tests for RAG engine instrumentation and output cleanup."""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["TINKER_MODEL_PATH"] = ""
os.environ["OPENAI_BASE_URL"] = ""
os.environ["OPENAI_API_KEY"] = ""

from app.services.rag_engine import RAGEngine


def test_sanitize_tinker_answer_removes_links_and_source_tail():
    raw = (
        "Available docs include [Doc A](https://example.com/a), "
        "Invoice_INV-2026-LOAD00036_Schneider_Schneider. "
        "Sources: BOL_A.pdf, BOL_B.pdf"
    )
    cleaned = RAGEngine._sanitize_tinker_answer(raw)
    assert "https://" not in cleaned
    assert "Sources:" not in cleaned
    assert "_Schneider_Schneider" not in cleaned
    assert "Doc A" in cleaned


def test_latency_metrics_report_percentiles_and_routes():
    engine = RAGEngine()
    for latency in [100.0, 200.0, 300.0, 400.0, 500.0]:
        engine._record_query_metric("llm_generation", latency, success=True)
    engine._record_query_metric("vector_search", 2600.0, success=False)

    metrics = engine.get_latency_metrics()
    assert metrics["status"] == "ok"
    assert metrics["samples_window"] >= 6
    assert metrics["p50_ms"] >= 300.0
    assert metrics["p95_ms"] >= 500.0
    assert metrics["routes"]["route:llm_generation"] >= 5
    assert metrics["routes"]["route:vector_search"] >= 1
    assert metrics["routes"]["success:yes"] >= 5
    assert metrics["routes"]["success:no"] >= 1
