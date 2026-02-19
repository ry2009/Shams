#!/usr/bin/env python3
"""Run inference against a Tinker checkpoint (base or fine-tuned)."""

from __future__ import annotations

import argparse
import os
from typing import Any

from tinker import ModelInput, SamplingParams, ServiceClient


def _encode(tokenizer: Any, text: str) -> list[int]:
    try:
        return [int(x) for x in tokenizer.encode(text, add_special_tokens=False)]
    except TypeError:
        return [int(x) for x in tokenizer.encode(text)]


def _decode(tokenizer: Any, tokens: list[int]) -> str:
    try:
        return tokenizer.decode(tokens, skip_special_tokens=True)
    except TypeError:
        return tokenizer.decode(tokens)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample from a Tinker model checkpoint")
    parser.add_argument(
        "--model-path",
        required=True,
        help="Tinker model/checkpoint path, e.g. tinker://.../sampler_weights/...",
    )
    parser.add_argument("--prompt", required=True, help="Prompt text")
    parser.add_argument("--max-tokens", type=int, default=220)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--top-k", type=int, default=40)
    parser.add_argument(
        "--num-samples",
        type=int,
        default=1,
        help="Number of generations to request",
    )
    args = parser.parse_args()

    if not os.getenv("TINKER_API_KEY"):
        raise SystemExit("TINKER_API_KEY is not set")

    service_client = ServiceClient(user_metadata={"project": "shams_trucking_sft"})
    sampling_client = service_client.create_sampling_client(model_path=args.model_path)
    tokenizer = sampling_client.get_tokenizer()

    prompt_tokens = _encode(tokenizer, args.prompt)
    response = sampling_client.sample(
        prompt=ModelInput.from_ints(prompt_tokens),
        num_samples=max(1, args.num_samples),
        sampling_params=SamplingParams(
            max_tokens=max(1, args.max_tokens),
            temperature=args.temperature,
            top_p=args.top_p,
            top_k=args.top_k,
        ),
    ).result()

    for i, seq in enumerate(response.sequences, start=1):
        print(f"\n--- sample {i} (stop={seq.stop_reason}) ---")
        print(_decode(tokenizer, seq.tokens).strip())


if __name__ == "__main__":
    main()
