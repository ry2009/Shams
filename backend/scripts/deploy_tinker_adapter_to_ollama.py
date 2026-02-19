#!/usr/bin/env python3
"""Download a Tinker adapter checkpoint and create a local Ollama model."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import httpx
from tinker import ServiceClient


def _slug_from_checkpoint_path(checkpoint_path: str) -> str:
    trimmed = checkpoint_path.replace("tinker://", "").replace("/", "_").replace(":", "_")
    return trimmed


def _download_and_extract(checkpoint_path: str, output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    slug = _slug_from_checkpoint_path(checkpoint_path)
    target_dir = output_root / slug
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    client = ServiceClient()
    rest = client.create_rest_client()
    archive_url = rest.get_checkpoint_archive_url_from_tinker_path(checkpoint_path).result().url

    archive_file = target_dir / "archive.tar"
    with httpx.Client(timeout=180.0, verify=False, follow_redirects=True) as client:
        with client.stream("GET", archive_url) as resp:
            resp.raise_for_status()
            with archive_file.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

    with tarfile.open(archive_file, "r") as tar:
        tar.extractall(target_dir)
    archive_file.unlink(missing_ok=True)
    return target_dir


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({' '.join(cmd)}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    if proc.stdout.strip():
        print(proc.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy a Tinker LoRA adapter to Ollama")
    parser.add_argument("--checkpoint-path", required=True, help="tinker://... sampler_weights checkpoint path")
    parser.add_argument(
        "--base-model",
        default="llama3.2:1b",
        help="Local Ollama base model to apply adapter onto",
    )
    parser.add_argument(
        "--ollama-model-name",
        default="shams-trucking-finetuned",
        help="Name for created local Ollama model",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/finetune/adapters"),
        help="Where to store downloaded adapter files",
    )
    args = parser.parse_args()

    print("Downloading checkpoint archive...")
    adapter_dir = _download_and_extract(args.checkpoint_path, args.output_dir.resolve())

    adapter_file = adapter_dir / "adapter_model.safetensors"
    adapter_config = adapter_dir / "adapter_config.json"
    if not adapter_file.exists() or not adapter_config.exists():
        raise SystemExit(f"Adapter files missing in {adapter_dir}")

    modelfile = adapter_dir / "Modelfile"
    modelfile.write_text(
        "\n".join(
            [
                f"FROM {args.base_model}",
                f"ADAPTER {adapter_dir}",
                "PARAMETER temperature 0.1",
                "SYSTEM You are a trucking operations copilot. Be concise, factual, and include source filenames when provided in context.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(f"Ensuring base model exists: {args.base_model}")
    _run(["ollama", "pull", args.base_model])

    print(f"Creating Ollama model: {args.ollama_model_name}")
    _run(["ollama", "create", args.ollama_model_name, "-f", str(modelfile)])

    summary = {
        "checkpoint_path": args.checkpoint_path,
        "base_model": args.base_model,
        "ollama_model_name": args.ollama_model_name,
        "adapter_dir": str(adapter_dir),
        "adapter_file": str(adapter_file),
        "modelfile": str(modelfile),
    }
    summary_file = adapter_dir / "deploy_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\nDeployment complete")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
