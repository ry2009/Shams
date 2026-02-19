"""Smoke tests for performance tooling scripts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_vector_kernel_benchmark_script_runs_and_emits_report(tmp_path: Path):
    backend_root = Path(__file__).resolve().parents[1]
    report_path = tmp_path / "vector_kernel_report.json"
    cmd = [
        sys.executable,
        "scripts/benchmark_vector_kernel.py",
        "--chunks",
        "2000",
        "--dim",
        "64",
        "--queries",
        "12",
        "--top-k",
        "6",
        "--target-p95-ms",
        "120",
        "--output",
        str(report_path),
    ]

    proc = subprocess.run(
        cmd,
        cwd=str(backend_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"benchmark failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert report_path.exists()

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["samples"] == 12
    assert payload["kernel"]["type"] == "numpy_cosine_kernel"
    assert payload["pass"] is True
