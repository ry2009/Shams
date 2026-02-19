#!/usr/bin/env python3
"""Benchmark the vector-store similarity kernel with repeatable synthetic data."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.models.document import Document, DocumentType
from app.services.vector_store import VectorStore


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int((len(ordered) - 1) * p)
    return ordered[max(0, min(len(ordered) - 1, idx))]


def random_unit_vector(dim: int) -> list[float]:
    vec = [random.uniform(-1.0, 1.0) for _ in range(dim)]
    norm = sum(v * v for v in vec) ** 0.5
    if norm <= 0:
        return [0.0 for _ in range(dim)]
    return [v / norm for v in vec]


async def seed_index(store: VectorStore, tenant_id: str, chunks: int, dim: int) -> None:
    doc = Document(
        id="bench-doc",
        filename="bench.pdf",
        document_type=DocumentType.BOL,
        raw_text="benchmark",
    )
    rows = [(f"chunk-{i}", {"chunk_index": i}) for i in range(chunks)]
    embeddings = [random_unit_vector(dim) for _ in range(chunks)]
    await store.add_document_chunks(doc, rows, embeddings, tenant_id=tenant_id)


async def benchmark(store: VectorStore, tenant_id: str, queries: int, top_k: int, dim: int) -> dict:
    latencies_ms: list[float] = []
    for _ in range(queries):
        query = random_unit_vector(dim)
        started = time.perf_counter()
        results = await store.search(query_embedding=query, top_k=top_k, tenant_id=tenant_id)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        latencies_ms.append(elapsed_ms)
        if not results:
            raise RuntimeError("Vector benchmark search returned no results.")

    stats = store.get_stats(tenant_id=tenant_id)
    return {
        "samples": len(latencies_ms),
        "avg_ms": round(statistics.mean(latencies_ms), 4),
        "p50_ms": round(percentile(latencies_ms, 0.50), 4),
        "p95_ms": round(percentile(latencies_ms, 0.95), 4),
        "max_ms": round(max(latencies_ms), 4),
        "min_ms": round(min(latencies_ms), 4),
        "kernel": stats.get("kernel", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark SHAMS vector kernel performance")
    parser.add_argument("--tenant-id", default="bench")
    parser.add_argument("--chunks", type=int, default=20000)
    parser.add_argument("--dim", type=int, default=768)
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--target-p95-ms", type=float, default=8.0)
    parser.add_argument("--output", type=Path, help="Optional output JSON report path")
    args = parser.parse_args()

    random.seed(args.seed)

    with tempfile.TemporaryDirectory(prefix="shams-vector-bench-") as tmp:
        index_path = Path(tmp) / "vector_index.jsonl"
        os.environ["VECTOR_INDEX_PATH"] = str(index_path)
        get_settings.cache_clear()

        store = VectorStore()
        asyncio.run(seed_index(store, args.tenant_id, max(1, args.chunks), max(8, args.dim)))
        report = asyncio.run(
            benchmark(
                store=store,
                tenant_id=args.tenant_id,
                queries=max(1, args.queries),
                top_k=max(1, args.top_k),
                dim=max(8, args.dim),
            )
        )

    report["target_p95_ms"] = args.target_p95_ms
    report["pass"] = bool(report["p95_ms"] <= args.target_p95_ms)
    print(json.dumps(report, indent=2))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
