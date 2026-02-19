#!/usr/bin/env python3
"""Build a small supervised fine-tuning dataset from trucking PDF docs."""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import PyPDF2


BROKER_ALIAS_TO_NAME = {
    "tql": "Total Quality Logistics, LLC",
    "coyote": "Coyote Logistics, LLC",
    "xpo": "XPO Logistics Freight, Inc.",
    "schneider": "Schneider National Carriers, Inc.",
    "landstar": "Landstar Inway, Inc.",
    "jbhunt": "J.B. Hunt Transport, Inc.",
    "uber freight": "Uber Freight",
    "uber_freight": "Uber Freight",
    "convoy": "Convoy Inc.",
}


LOAD_ID_RE = re.compile(r"\b(LOAD\d{5})\b", re.IGNORECASE)
INVOICE_NO_RE = re.compile(r"Invoice\s*#:\s*([A-Z0-9-]+)", re.IGNORECASE)
RATE_CONF_RE = re.compile(r"(?:Confirmation|Rate\s*Conf(?:irmation)?)\s*#:\s*(RC[0-9A-Z-]+)", re.IGNORECASE)
MONEY_RE = re.compile(r"\$([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})|[0-9]+\.[0-9]{2})")
TOTAL_DUE_RE = re.compile(r"TOTAL\s+DUE\s+\$([0-9,]+\.[0-9]{2})", re.IGNORECASE)
TOTAL_RATE_RE = re.compile(r"(?:Total\s+Rate|TOTAL\s+RATE)\s+\$([0-9,]+\.[0-9]{2})", re.IGNORECASE)
RATE_PER_MILE_RE = re.compile(r"(?:Rate\/?Mile|Rate\s*per\s*mile)\s+\$([0-9,]+\.[0-9]{2})", re.IGNORECASE)
BROKER_LINE_RE = re.compile(r"Broker:\s*([^\n]+?)\s+MC\s*#?:", re.IGNORECASE)
BROKER_BILL_TO_RE = re.compile(r"BILL TO:\s*([^\n]+)", re.IGNORECASE)
BROKER_BILL_TO_NEXT_LINE_RE = re.compile(r"BILL TO:\s*(?:REMIT TO:\s*)?\n([^\n]+)", re.IGNORECASE)
RATE_SUMMARY_ROW_RE = re.compile(
    r"Line Haul[\s\S]{0,200}?\$([0-9,]+\.[0-9]{2})\s+\$([0-9,]+\.[0-9]{2})\s+\$([0-9,]+\.[0-9]{2})\s+\$([0-9,]+\.[0-9]{2})",
    re.IGNORECASE,
)


@dataclass
class LoadFacts:
    load_id: str
    broker_name: str | None = None
    invoice_number: str | None = None
    invoice_amount: float | None = None
    rate_conf_number: str | None = None
    total_rate: float | None = None
    rate_per_mile: float | None = None
    invoice_filename: str | None = None
    rate_conf_filename: str | None = None


def _extract_pdf_text(path: Path) -> str:
    parts: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text:
                    parts.append(text)
        return "\n".join(parts)
    except Exception:
        with path.open("rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                parts.append(page.extract_text() or "")
        return "\n".join(parts)


def _first_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _load_id_from_text_or_name(text: str, filename: str) -> str | None:
    match = LOAD_ID_RE.search(text) or LOAD_ID_RE.search(filename)
    if not match:
        return None
    return match.group(1).upper()


def _broker_from_alias(filename: str) -> str | None:
    stem = Path(filename).stem.lower()
    for alias, full in BROKER_ALIAS_TO_NAME.items():
        if alias in stem:
            return full
    return None


def _parse_invoice(path: Path) -> tuple[str | None, dict]:
    text = _extract_pdf_text(path)
    load_id = _load_id_from_text_or_name(text, path.name)
    invoice_no = _first_group(INVOICE_NO_RE, text)
    total_due = _to_float(_first_group(TOTAL_DUE_RE, text))
    broker = _first_group(BROKER_BILL_TO_NEXT_LINE_RE, text) or _first_group(BROKER_BILL_TO_RE, text)
    if broker:
        broker = broker.strip().strip(":")
        broker = broker.split("[", 1)[0].strip()
    if not broker or broker.lower() in {"remit to", "remit to:", "bill to"}:
        broker = _broker_from_alias(path.name)
    return load_id, {
        "broker_name": broker,
        "invoice_number": invoice_no,
        "invoice_amount": total_due,
        "invoice_filename": path.name,
    }


def _parse_rate_conf(path: Path) -> tuple[str | None, dict]:
    text = _extract_pdf_text(path)
    load_id = _load_id_from_text_or_name(text, path.name)
    broker = _first_group(BROKER_LINE_RE, text) or _broker_from_alias(path.name)
    rate_conf_no = _first_group(RATE_CONF_RE, text)
    summary_match = RATE_SUMMARY_ROW_RE.search(text)
    total_rate = None
    rate_per_mile = None
    if summary_match:
        total_rate = _to_float(summary_match.group(3))
        rate_per_mile = _to_float(summary_match.group(4))
    if total_rate is None:
        total_rate = _to_float(_first_group(TOTAL_RATE_RE, text))
    if rate_per_mile is None:
        rate_per_mile = _to_float(_first_group(RATE_PER_MILE_RE, text))

    if total_rate is None:
        monies = [float(v.replace(",", "")) for v in MONEY_RE.findall(text)]
        if monies:
            total_rate = max(monies)

    return load_id, {
        "broker_name": broker,
        "rate_conf_number": rate_conf_no,
        "total_rate": total_rate,
        "rate_per_mile": rate_per_mile,
        "rate_conf_filename": path.name,
    }


def _merge_facts(base: LoadFacts, patch: dict) -> LoadFacts:
    for key, value in patch.items():
        if value is None:
            continue
        if getattr(base, key) is None:
            setattr(base, key, value)
    return base


def _format_money(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"${value:,.2f}"


def _build_examples(loads: dict[str, LoadFacts]) -> list[dict]:
    examples: list[dict] = []

    refusal_qas = [
        (
            "Question: who's the broker and what's the invoice?\nAnswer:",
            " I need a specific load ID (for example LOAD00030) to answer accurately.",
        ),
        (
            "Question: rate details?\nAnswer:",
            " Please include the load ID so I can return exact rate details with source documents.",
        ),
    ]

    for idx, (load_id, facts) in enumerate(sorted(loads.items())):
        broker = facts.broker_name or "unknown"
        invoice_no = facts.invoice_number or "unknown"
        invoice_amt = _format_money(facts.invoice_amount)
        total_rate = _format_money(facts.total_rate)
        rpm = f"${facts.rate_per_mile:.2f}" if facts.rate_per_mile is not None else "unknown"
        rate_conf_no = facts.rate_conf_number or "unknown"

        sources = [s for s in [facts.invoice_filename, facts.rate_conf_filename] if s]
        source_text = "; ".join(sources) if sources else "unknown source"

        qa_pairs = [
            (
                f"Question: For load {load_id}, who's the broker and what's the invoice number?\nAnswer:",
                f" Broker: {broker}. Invoice number: {invoice_no}. Sources: {source_text}.",
            ),
            (
                f"Question: What is the invoice total for load {load_id}?\nAnswer:",
                f" Invoice total for {load_id}: {invoice_amt}. Source: {facts.invoice_filename or 'unknown'}.",
            ),
            (
                f"Question: Give me rate details for load {load_id}.\nAnswer:",
                f" Load {load_id} total rate: {total_rate}; rate per mile: {rpm}; rate confirmation: {rate_conf_no}. Sources: {source_text}.",
            ),
            (
                f"Question: Summarize AP facts for load {load_id}.\nAnswer:",
                f" Load {load_id}: broker {broker}, invoice {invoice_no} ({invoice_amt}), rate confirmation {rate_conf_no}, linehaul total {total_rate}. Sources: {source_text}.",
            ),
        ]

        for variant_idx, (prompt, completion) in enumerate(qa_pairs):
            examples.append(
                {
                    "id": f"{load_id}_qa_{variant_idx}",
                    "load_id": load_id,
                    "prompt": prompt,
                    "completion": completion,
                    "sources": sources,
                }
            )

        if idx % 8 == 0:
            r_prompt, r_completion = refusal_qas[idx % len(refusal_qas)]
            examples.append(
                {
                    "id": f"refusal_{idx}",
                    "load_id": None,
                    "prompt": r_prompt,
                    "completion": r_completion,
                    "sources": [],
                }
            )

    random.shuffle(examples)
    return examples


def main() -> None:
    parser = argparse.ArgumentParser(description="Build SFT dataset from trucking documents")
    parser.add_argument(
        "--docs-root",
        type=Path,
        default=Path("../sample_data/documents"),
        help="Root directory containing docs folders",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/finetune"),
        help="Output directory for train/eval JSONL",
    )
    parser.add_argument("--eval-ratio", type=float, default=0.1, help="Holdout ratio")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    docs_root = args.docs_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    invoice_files = sorted((docs_root / "invoices").glob("*.pdf"))
    rate_files = sorted((docs_root / "rate_cons").glob("*.pdf"))

    loads: dict[str, LoadFacts] = {}

    for invoice in invoice_files:
        load_id, patch = _parse_invoice(invoice)
        if not load_id:
            continue
        facts = loads.setdefault(load_id, LoadFacts(load_id=load_id))
        _merge_facts(facts, patch)

    for rate in rate_files:
        load_id, patch = _parse_rate_conf(rate)
        if not load_id:
            continue
        facts = loads.setdefault(load_id, LoadFacts(load_id=load_id))
        _merge_facts(facts, patch)

    examples = _build_examples(loads)
    split_idx = int(len(examples) * (1.0 - args.eval_ratio))
    train_examples = examples[:split_idx]
    eval_examples = examples[split_idx:]

    train_path = output_dir / "train.jsonl"
    eval_path = output_dir / "eval.jsonl"
    summary_path = output_dir / "summary.json"

    with train_path.open("w", encoding="utf-8") as f:
        for row in train_examples:
            f.write(json.dumps(row) + "\n")

    with eval_path.open("w", encoding="utf-8") as f:
        for row in eval_examples:
            f.write(json.dumps(row) + "\n")

    summary = {
        "docs_root": str(docs_root),
        "load_count": len(loads),
        "invoice_count": len(invoice_files),
        "rate_conf_count": len(rate_files),
        "example_count_total": len(examples),
        "example_count_train": len(train_examples),
        "example_count_eval": len(eval_examples),
        "train_file": str(train_path),
        "eval_file": str(eval_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
