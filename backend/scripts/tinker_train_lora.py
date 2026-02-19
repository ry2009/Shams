#!/usr/bin/env python3
"""Train a small LoRA adapter on trucking QA pairs using Tinker."""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime
from pathlib import Path

from tinker import AdamParams, Datum, ModelInput, ServiceClient, TensorData


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _encode(tokenizer, text: str) -> list[int]:
    try:
        return [int(x) for x in tokenizer.encode(text, add_special_tokens=False)]
    except TypeError:
        return [int(x) for x in tokenizer.encode(text)]


def _build_datum(tokenizer, prompt: str, completion: str) -> Datum:
    prompt_tokens = _encode(tokenizer, prompt)
    completion_tokens = _encode(tokenizer, completion)

    # Tinker cross-entropy expects target/weights tensors to match model_input length.
    target_tokens = prompt_tokens + completion_tokens
    bos = getattr(tokenizer, "bos_token_id", None) or getattr(tokenizer, "eos_token_id", None) or 0
    model_input_tokens = [int(bos)] + target_tokens[:-1]
    weights = [0.0] * len(prompt_tokens) + [1.0] * len(completion_tokens)

    if not (len(model_input_tokens) == len(target_tokens) == len(weights)):
        raise ValueError("Invalid tensor lengths for datum")

    return Datum(
        model_input=ModelInput.from_ints(model_input_tokens),
        loss_fn_inputs={
            "target_tokens": TensorData(
                data=target_tokens,
                dtype="int64",
                shape=[len(target_tokens)],
            ),
            "weights": TensorData(
                data=weights,
                dtype="float32",
                shape=[len(weights)],
            ),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune a Tinker LoRA adapter from JSONL prompt/completion data")
    parser.add_argument(
        "--train-file",
        type=Path,
        default=Path("./data/finetune/train.jsonl"),
        help="Path to training JSONL with prompt/completion fields",
    )
    parser.add_argument(
        "--base-model",
        type=str,
        default="meta-llama/Llama-3.2-1B",
        help="Tinker base model name",
    )
    parser.add_argument("--rank", type=int, default=8, help="LoRA rank")
    parser.add_argument("--steps", type=int, default=80, help="Training steps")
    parser.add_argument("--batch-size", type=int, default=4, help="Batch size")
    parser.add_argument("--learning-rate", type=float, default=1e-4, help="Adam learning rate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--log-every", type=int, default=10, help="Log frequency")
    parser.add_argument(
        "--save-name",
        type=str,
        default="shams_trucking_sft",
        help="Checkpoint name prefix",
    )
    parser.add_argument(
        "--metadata-out",
        type=Path,
        default=Path("./data/finetune/train_run_metadata.json"),
        help="Where to write training metadata JSON",
    )
    args = parser.parse_args()

    random.seed(args.seed)
    train_rows = _load_jsonl(args.train_file)
    if not train_rows:
        raise SystemExit(f"No training rows found in {args.train_file}")

    print(f"Loaded {len(train_rows)} training rows")
    print(f"Creating training client on {args.base_model} (rank={args.rank})...")

    service_client = ServiceClient(user_metadata={"project": "shams_trucking_sft"})
    training_client = service_client.create_lora_training_client(
        base_model=args.base_model,
        rank=args.rank,
        train_mlp=True,
        train_attn=True,
        train_unembed=False,
    )
    model_info = training_client.get_info()
    tokenizer = training_client.get_tokenizer()

    print("Training client ready")
    print(f"Model ID: {model_info.model_id}")

    losses: list[float] = []

    for step in range(1, args.steps + 1):
        batch = random.choices(train_rows, k=args.batch_size)
        datums = [_build_datum(tokenizer, row["prompt"], row["completion"]) for row in batch]

        fw_result = training_client.forward_backward(datums, "cross_entropy").result()
        loss_sum = float((fw_result.metrics or {}).get("loss:sum", 0.0))
        losses.append(loss_sum / max(1, args.batch_size))

        training_client.optim_step(
            AdamParams(
                learning_rate=args.learning_rate,
                grad_clip_norm=1.0,
                weight_decay=0.0,
            )
        ).result()

        if step % args.log_every == 0 or step == 1 or step == args.steps:
            window = losses[-args.log_every :] if len(losses) >= args.log_every else losses
            avg_loss = sum(window) / len(window)
            print(f"[step {step:04d}] avg_loss={avg_loss:.4f}")

    ckpt_name = f"{args.save_name}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    save_result = training_client.save_weights_for_sampler(ckpt_name).result()
    checkpoint_path = save_result.path

    metadata = {
        "created_at_utc": datetime.utcnow().isoformat() + "Z",
        "base_model": args.base_model,
        "rank": args.rank,
        "steps": args.steps,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "train_rows": len(train_rows),
        "model_id": model_info.model_id,
        "checkpoint_path": checkpoint_path,
        "loss_last": losses[-1] if losses else None,
        "loss_avg": (sum(losses) / len(losses)) if losses else None,
    }

    args.metadata_out.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_out.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print("\nTraining complete")
    print(f"Checkpoint: {checkpoint_path}")
    print(f"Metadata: {args.metadata_out}")


if __name__ == "__main__":
    main()
