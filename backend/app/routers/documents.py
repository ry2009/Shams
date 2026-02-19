"""API routes for document operations."""
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Query, Depends
from typing import List, Optional

from app.models.document import (
    Document, DocumentType, DocumentStatus,
    ExtractionRequest, ExtractionResponse
)
from app.services.document_processor import document_processor
from app.services.embeddings import embedding_service
from app.services.vector_store import vector_store
from app.services.extraction import extraction_service
from app.services.document_registry import document_registry
from app.core.auth import TenantContext, get_tenant_context
from app.core.logging import logger

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: Optional[str] = Form(None),
    context: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Upload and process a document."""
    
    # Validate file size (50MB limit)
    MAX_SIZE = 50 * 1024 * 1024
    content = await file.read()
    
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")
    
    # Parse document type
    doc_type = None
    if document_type:
        try:
            doc_type = DocumentType(document_type)
        except ValueError:
            pass
    
    # Process document
    document = await document_processor.process_file(
        file_content=content,
        filename=file.filename,
        document_type=doc_type
    )
    
    if document.status == DocumentStatus.ERROR:
        raise HTTPException(status_code=500, detail="Document processing failed")
    
    # Extract structured data
    document = await extraction_service.extract_all(document)
    document.metadata["tenant_id"] = context.tenant_id
    document_registry.upsert(document, tenant_id=context.tenant_id)
    
    # Chunk and embed in background
    background_tasks.add_task(_process_embeddings, document, context.tenant_id)
    
    return {
        "document_id": document.id,
        "filename": document.filename,
        "document_type": document.document_type.value,
        "status": document.status.value,
        "extracted_data": document.extracted_data,
        "load_ids": document_registry.get(document.id, tenant_id=context.tenant_id).get("load_ids", []),
        "tenant_id": context.tenant_id,
        "message": "Document uploaded and processing. Embeddings will be ready shortly."
    }


async def _process_embeddings(document: Document, tenant_id: str):
    """Background task to chunk and embed document."""
    try:
        # Chunk the text
        chunks = document_processor.chunk_text(
            document.raw_text,
            chunk_size=1000,
            chunk_overlap=200
        )
        
        if not chunks:
            logger.warning("No chunks generated for document", doc_id=document.id)
            return
        
        # Generate embeddings
        texts = [chunk[0] for chunk in chunks]
        embeddings = await embedding_service.embed_batch(texts)
        
        # Add to vector store
        await vector_store.add_document_chunks(document, chunks, embeddings, tenant_id=tenant_id)
        
        logger.info(
            "Document embeddings completed",
            doc_id=document.id,
            chunks=len(chunks)
        )
        
    except Exception as e:
        logger.error("Embedding processing failed", doc_id=document.id, error=str(e))


@router.get("/types")
async def get_document_types(context: TenantContext = Depends(get_tenant_context)) -> List[dict]:
    """Get list of available document types."""
    return [
        {"value": dt.value, "label": dt.value.replace("_", " ").title()}
        for dt in DocumentType
    ]


@router.post("/extract")
async def extract_document_data(
    request: ExtractionRequest,
    context: TenantContext = Depends(get_tenant_context),
) -> ExtractionResponse:
    """Extract structured data from an already processed document."""
    record = document_registry.get(request.document_id, tenant_id=context.tenant_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")

    requested_type = request.extraction_type.strip().lower()
    type_map = {
        "rate_confirmation": DocumentType.RATE_CONFIRMATION,
        "invoice": DocumentType.INVOICE,
        "proof_of_delivery": DocumentType.POD,
        "pod": DocumentType.POD,
        "bill_of_lading": DocumentType.BOL,
        "bol": DocumentType.BOL,
        "lumper_receipt": DocumentType.LUMPER_RECEIPT,
        "lumper": DocumentType.LUMPER_RECEIPT,
    }
    target_type = type_map.get(requested_type)
    if not target_type:
        raise HTTPException(status_code=400, detail="Unsupported extraction type")

    created_at = datetime.fromisoformat(record["created_at"]) if record.get("created_at") else datetime.utcnow()
    processed_at = datetime.fromisoformat(record["processed_at"]) if record.get("processed_at") else None

    document = Document(
        id=record["id"],
        filename=record["filename"],
        document_type=target_type,
        status=DocumentStatus.PROCESSED,
        raw_text=record.get("raw_text", ""),
        extracted_data={},
        metadata=record.get("metadata", {}),
        created_at=created_at,
        processed_at=processed_at,
    )
    document = await extraction_service.extract_all(document)
    document.metadata["tenant_id"] = context.tenant_id
    document_registry.upsert(document, tenant_id=context.tenant_id)

    return ExtractionResponse(
        document_id=document.id,
        extraction_type=request.extraction_type,
        data=document.extracted_data,
        confidence=0.9 if document.extracted_data else 0.0,
        raw_text_preview=document.raw_text[:500],
    )


@router.get("/stats")
async def get_document_stats(context: TenantContext = Depends(get_tenant_context)) -> dict:
    """Get document and vector store statistics."""
    vector_stats = vector_store.get_stats(tenant_id=context.tenant_id)
    registry_stats = document_registry.get_stats(tenant_id=context.tenant_id)
    return {
        "vector_store": vector_stats,
        "registry": registry_stats,
        "tenant_id": context.tenant_id,
    }


@router.get("")
async def list_documents(
    document_type: Optional[DocumentType] = None,
    load_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    context: TenantContext = Depends(get_tenant_context),
) -> dict:
    """List documents from the persistent registry."""
    return {
        "documents": document_registry.list(
            tenant_id=context.tenant_id,
            document_type=document_type,
            load_id=load_id,
            limit=limit,
        ),
        "tenant_id": context.tenant_id,
    }


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    context: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Get a single document from the registry."""
    record = document_registry.get(document_id, tenant_id=context.tenant_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")
    return record


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    context: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Delete a document and its embeddings."""
    await vector_store.delete_document(document_id, tenant_id=context.tenant_id)
    document_registry.delete(document_id, tenant_id=context.tenant_id)
    return {"message": "Document deleted", "document_id": document_id}
