"""API routes for external system ingestion integrations."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import TenantContext, get_tenant_context
from app.core.logging import logger
from app.models.document import DocumentStatus
from app.services.document_processor import document_processor
from app.services.embeddings import embedding_service
from app.services.extraction import extraction_service
from app.services.vector_store import vector_store
from app.services.document_registry import document_registry
from app.services.microsoft_graph import microsoft_graph_service, MicrosoftGraphError


router = APIRouter(prefix="/integrations", tags=["integrations"])

DEFAULT_ALLOWED_EXTS = [".pdf", ".txt", ".eml", ".png", ".jpg", ".jpeg", ".heic", ".heif", ".html", ".htm"]


class OutlookImportRequest(BaseModel):
    folder: str = "inbox"
    days_back: int = Field(default=7, ge=0, le=180)
    max_messages: int = Field(default=50, ge=1, le=200)
    max_attachments: int = Field(default=150, ge=1, le=500)
    sender_contains: Optional[str] = None
    allowed_extensions: List[str] = Field(default_factory=lambda: DEFAULT_ALLOWED_EXTS.copy())


class TeamsImportRequest(BaseModel):
    root_path: str = ""
    recursive: bool = True
    max_files: int = Field(default=120, ge=1, le=500)
    allowed_extensions: List[str] = Field(default_factory=lambda: DEFAULT_ALLOWED_EXTS.copy())


def _infer_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def _normalize_allowed_exts(values: List[str]) -> set[str]:
    normalized = set()
    for value in values:
        ext = value.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        normalized.add(ext)
    return normalized


async def _ingest_bytes(
    *,
    file_content: bytes,
    filename: str,
    tenant_id: str,
    metadata_extra: Dict[str, str],
) -> Dict[str, object]:
    document = await document_processor.process_file(
        file_content=file_content,
        filename=filename,
        document_type=None,
    )
    if document.status == DocumentStatus.ERROR:
        raise RuntimeError(f"Document processing failed for {filename}")

    document = await extraction_service.extract_all(document)
    document.metadata["tenant_id"] = tenant_id
    document.metadata.update(metadata_extra)
    record = document_registry.upsert(document, tenant_id=tenant_id)

    chunks = document_processor.chunk_text(document.raw_text, chunk_size=1000, chunk_overlap=200)
    if chunks:
        embeddings = await embedding_service.embed_batch([chunk_text for chunk_text, _ in chunks])
        await vector_store.add_document_chunks(document, chunks, embeddings, tenant_id=tenant_id)

    return {
        "document_id": record.get("id"),
        "filename": record.get("filename"),
        "document_type": record.get("document_type"),
        "load_ids": record.get("load_ids", []),
        "chunks": len(chunks),
    }


@router.get("/microsoft/status")
async def microsoft_status(
    context: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Check Graph integration configuration status."""
    return {
        "tenant_id": context.tenant_id,
        "configured": microsoft_graph_service.is_configured(),
    }


@router.post("/microsoft/outlook/import")
async def import_outlook_attachments(
    request: OutlookImportRequest,
    context: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Import Outlook email attachments from Microsoft Graph."""
    try:
        messages = await microsoft_graph_service.list_recent_messages(
            folder=request.folder,
            days_back=request.days_back,
            max_messages=request.max_messages,
            sender_contains=request.sender_contains,
        )
    except MicrosoftGraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Outlook import failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    allowed_exts = _normalize_allowed_exts(request.allowed_extensions)
    ingested = []
    skipped = 0

    for message in messages:
        if len(ingested) >= request.max_attachments:
            break
        if not message.get("hasAttachments"):
            continue

        try:
            attachments = await microsoft_graph_service.get_message_attachments(message["id"])
        except Exception as exc:
            logger.warning("Failed to read message attachments", message_id=message.get("id"), error=str(exc))
            continue

        for attachment in attachments:
            if len(ingested) >= request.max_attachments:
                break
            filename = attachment.get("filename", "attachment.bin")
            if _infer_extension(filename) not in allowed_exts:
                skipped += 1
                continue

            try:
                result = await _ingest_bytes(
                    file_content=attachment["bytes"],
                    filename=filename,
                    tenant_id=context.tenant_id,
                    metadata_extra={
                        "source": "outlook",
                        "outlook_message_id": message.get("id", ""),
                        "outlook_subject": message.get("subject", ""),
                        "outlook_received_at": message.get("receivedDateTime", ""),
                    },
                )
                ingested.append(result)
            except Exception as exc:
                logger.warning("Attachment ingest failed", filename=filename, error=str(exc))

    return {
        "tenant_id": context.tenant_id,
        "messages_scanned": len(messages),
        "documents_ingested": len(ingested),
        "skipped": skipped,
        "documents": ingested,
    }


@router.post("/microsoft/teams/import")
async def import_teams_files(
    request: TeamsImportRequest,
    context: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Import Teams/Drive files from Microsoft Graph."""
    try:
        files = await microsoft_graph_service.list_drive_items(
            root_path=request.root_path,
            recursive=request.recursive,
            max_files=request.max_files,
        )
    except MicrosoftGraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Teams import failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    allowed_exts = _normalize_allowed_exts(request.allowed_extensions)
    ingested = []
    skipped = 0

    for file_item in files:
        filename = file_item.get("name", "file.bin")
        if _infer_extension(filename) not in allowed_exts:
            skipped += 1
            continue

        try:
            content = await microsoft_graph_service.download_drive_file(file_item["id"])
            result = await _ingest_bytes(
                file_content=content,
                filename=filename,
                tenant_id=context.tenant_id,
                metadata_extra={
                    "source": "teams_drive",
                    "drive_item_id": file_item.get("id", ""),
                    "drive_web_url": file_item.get("web_url", ""),
                    "drive_last_modified": file_item.get("last_modified", ""),
                },
            )
            ingested.append(result)
        except Exception as exc:
            logger.warning("Drive file ingest failed", filename=filename, error=str(exc))

    return {
        "tenant_id": context.tenant_id,
        "files_scanned": len(files),
        "documents_ingested": len(ingested),
        "skipped": skipped,
        "documents": ingested,
    }
