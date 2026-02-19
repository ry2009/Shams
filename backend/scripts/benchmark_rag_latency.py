#!/usr/bin/env python3
"""Benchmark RAG endpoint latency with a repeatable query set."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import requests


DEFAULT_QUERIES = [
    "whos the broker and whats the invoice for load LOAD00030",
    "rate details for load LOAD00030",
    "summarize bol details for BOL174299",
    "what does the available document set mostly contain?",
]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = int((len(values) - 1) * pct)
    return values[max(0, min(len(values) - 1, idx))]


def run_benchmark(base_url: str, queries: list[str], iterations: int, timeout: float, cache_bust: bool) -> dict:
    latencies = []
    errors = []
    results = []

    for i in range(iterations):
        for query in queries:
            effective_query = query
            if cache_bust:
                effective_query = f"{query} [bench-{i + 1}]"
            started = time.time()
            try:
                response = requests.post(
                    f"{base_url.rstrip('/')}/rag/query",
                    json={"query": effective_query, "top_k": 3, "include_sources": True},
                    timeout=timeout,
                )
                elapsed_ms = (time.time() - started) * 1000
                payload = response.json()
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {payload}")

                latencies.append(elapsed_ms)
                results.append(
                    {
                        "iteration": i + 1,
                        "query": query,
                        "effective_query": effective_query,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "processing_time_ms": round(float(payload.get("processing_time_ms", 0.0)), 2),
                        "confidence": float(payload.get("confidence", 0.0)),
                        "answer_preview": str(payload.get("answer", ""))[:140],
                    }
                )
            except Exception as exc:
                elapsed_ms = (time.time() - started) * 1000
                errors.append(
                    {
                        "iteration": i + 1,
                        "query": query,
                        "effective_query": effective_query,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "error": str(exc),
                    }
                )

    summary = {
        "samples": len(latencies),
        "errors": len(errors),
        "avg_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "p50_ms": round(percentile(latencies, 0.50), 2) if latencies else 0.0,
        "p95_ms": round(percentile(latencies, 0.95), 2) if latencies else 0.0,
        "max_ms": round(max(latencies), 2) if latencies else 0.0,
        "min_ms": round(min(latencies), 2) if latencies else 0.0,
    }
    return {"summary": summary, "results": results, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark /rag/query latency")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--iterations", type=int, default=5, help="How many times to run each query")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds")
    parser.add_argument("--target-ms", type=float, default=3000.0, help="Pass/fail threshold for p95")
    parser.add_argument("--queries-file", type=Path, help="Optional JSON file with an array of query strings")
    parser.add_argument("--output", type=Path, help="Optional output path for JSON report")
    parser.add_argument(
        "--cache-bust",
        action="store_true",
        help="Append per-iteration marker to queries so every call is uncached",
    )
    args = parser.parse_args()

    if args.queries_file:
        queries = json.loads(args.queries_file.read_text(encoding="utf-8"))
        if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
            raise SystemExit("queries-file must be a JSON array of strings")
    else:
        queries = DEFAULT_QUERIES

    report = run_benchmark(
        base_url=args.base_url,
        queries=queries,
        iterations=max(1, args.iterations),
        timeout=max(1.0, args.timeout),
        cache_bust=args.cache_bust,
    )
    report["target_ms"] = args.target_ms
    report["pass"] = bool(report["summary"]["samples"] > 0 and report["summary"]["p95_ms"] <= args.target_ms and report["summary"]["errors"] == 0)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2))
    if report["errors"]:
        print("\nErrors:")
        for err in report["errors"][:10]:
            print(f"- iter {err['iteration']} | {err['query']} | {err['error']}")

    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
