#!/usr/bin/env python3
"""Bulk import documents into the Shams registry + vector store."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Iterable, Optional

# Ensure `app` package is importable when script is run directly.
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.document import DocumentType
from app.services.document_processor import document_processor
from app.services.embeddings import embedding_service
from app.services.extraction import extraction_service
from app.services.vector_store import vector_store
from app.services.document_registry import document_registry


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".eml", ".png", ".jpg", ".jpeg", ".html", ".htm"}


def infer_type(path: Path) -> Optional[DocumentType]:
    """Infer type from parent folder + filename when possible."""
    tokens = {token.lower() for token in path.parts}
    name = path.name.lower()

    if "rate_cons" in tokens or "ratecon" in name or "rateconf" in name:
        return DocumentType.RATE_CONFIRMATION
    if "invoices" in tokens or "invoice" in name:
        return DocumentType.INVOICE
    if "bols" in tokens or "bill_of_lading" in name or name.startswith("bol_"):
        return DocumentType.BOL
    if "pods" in tokens or name.startswith("pod_") or "proof_of_delivery" in name:
        return DocumentType.POD
    if "lumpers" in tokens or "lumper" in name:
        return DocumentType.LUMPER_RECEIPT
    if "emails" in tokens:
        return DocumentType.EMAIL
    if "guides" in tokens:
        return DocumentType.ROUTING_GUIDE
    if "policies" in tokens:
        return DocumentType.POLICY
    return None


async def ingest_file(path: Path, tenant_id: str = "demo", skip_embeddings: bool = False) -> bool:
    file_bytes = path.read_bytes()
    document = await document_processor.process_file(
        file_content=file_bytes,
        filename=path.name,
        document_type=infer_type(path),
    )
    if document.status.value == "error":
        print(f"[ERROR] {path}")
        return False

    document = await extraction_service.extract_all(document)
    document.metadata["tenant_id"] = tenant_id
    record = document_registry.upsert(document, tenant_id=tenant_id)

    chunks = document_processor.chunk_text(document.raw_text, chunk_size=1000, chunk_overlap=200)
    if chunks and not skip_embeddings:
        embeddings = await embedding_service.embed_batch([chunk_text for chunk_text, _ in chunks])
        await vector_store.add_document_chunks(document, chunks, embeddings, tenant_id=tenant_id)

    load_ids = ", ".join(record.get("load_ids", [])) or "-"
    print(
        f"[OK] {path.name} type={record.get('document_type')} "
        f"chunks={len(chunks)} load_ids={load_ids}"
    )
    return True


def iter_files(root: Path, limit: int) -> Iterable[Path]:
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        yield path
        count += 1
        if limit > 0 and count >= limit:
            break


async def run(root: Path, limit: int, tenant_id: str, skip_embeddings: bool) -> None:
    total = 0
    succeeded = 0

    for file_path in iter_files(root, limit):
        total += 1
        ok = await ingest_file(file_path, tenant_id=tenant_id, skip_embeddings=skip_embeddings)
        if ok:
            succeeded += 1

    print(
        f"\nImport complete: {succeeded}/{total} successful | "
        f"tenant={tenant_id} registry_docs={document_registry.get_stats(tenant_id=tenant_id).get('total_documents', 0)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk import trucking documents")
    parser.add_argument(
        "folder",
        type=Path,
        help="Folder containing trucking docs (rate cons, BOLs, PODs, invoices, etc.)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of files to import (0 = all)",
    )
    parser.add_argument(
        "--tenant-id",
        type=str,
        default="demo",
        help="Tenant ID for imported records",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip vector embedding generation (faster import for workflow-only demos)",
    )
    args = parser.parse_args()

    root = args.folder.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Folder not found: {root}")

    asyncio.run(
        run(
            root=root,
            limit=max(0, args.limit),
            tenant_id=args.tenant_id.strip() or "demo",
            skip_embeddings=args.skip_embeddings,
        )
    )


if __name__ == "__main__":
    main()
