"""Document processing service - PDF, images, text extraction."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pdfplumber
from app.models.document import Document, DocumentType, DocumentStatus
from app.core.logging import logger

# Try to import magic, otherwise use extension-based detection.
try:
    import magic
    HAS_MAGIC = True
except (ImportError, Exception):
    HAS_MAGIC = False
    logger.warning("libmagic not available; using extension-based file type detection")

try:
    from PIL import Image

    HAS_PIL = True
except (ImportError, Exception):
    HAS_PIL = False
    logger.warning("Pillow not available, image OCR pipeline disabled")

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HAS_HEIF = True
except (ImportError, Exception):
    HAS_HEIF = False
    logger.warning("pillow-heif not available, HEIC conversion disabled")

try:
    import pytesseract

    HAS_TESSERACT = True
except (ImportError, Exception):
    HAS_TESSERACT = False
    logger.warning("pytesseract not available; OCR for images will fail until installed")


class DocumentProcessor:
    """Process various document formats into raw text."""
    
    # MIME type to document type mapping
    MIME_TYPES = {
        "application/pdf": DocumentType.RATE_CONFIRMATION,
        "text/plain": DocumentType.OTHER,
        "text/html": DocumentType.EMAIL,
        "message/rfc822": DocumentType.EMAIL,
        "image/png": DocumentType.POD,
        "image/jpeg": DocumentType.POD,
        "image/heic": DocumentType.POD,
        "image/heif": DocumentType.POD,
    }
    
    # Extension to MIME type mapping when libmagic is unavailable.
    EXT_TO_MIME = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".html": "text/html",
        ".htm": "text/html",
        ".eml": "message/rfc822",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".heic": "image/heic",
        ".heif": "image/heif",
    }
    
    def __init__(self, upload_dir: str = "./uploads"):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
    
    async def process_file(
        self, 
        file_content: bytes, 
        filename: str,
        document_type: DocumentType | None = None
    ) -> Document:
        """Process an uploaded file and extract text."""
        
        # Generate document ID
        doc_id = hashlib.md5(f"{filename}{datetime.utcnow().isoformat()}".encode()).hexdigest()[:12]
        
        # Detect MIME type
        if HAS_MAGIC:
            mime_type = magic.from_buffer(file_content, mime=True)
        else:
            # Use extension-based detection when libmagic is unavailable.
            ext = Path(filename).suffix.lower()
            mime_type = self.EXT_TO_MIME.get(ext, "application/octet-stream")
        
        # Normalize HEIC/HEIF to JPEG bytes when possible.
        if mime_type in {"image/heic", "image/heif"}:
            if not (HAS_PIL and HAS_HEIF):
                raise RuntimeError(
                    "HEIC/HEIF ingestion requires pillow + pillow-heif runtime dependencies."
                )
            normalized = self._convert_heic_to_jpeg(file_content)
            if normalized is None:
                raise RuntimeError("Failed to convert HEIC/HEIF image to JPEG.")
            file_content = normalized
            mime_type = "image/jpeg"
            filename = f"{Path(filename).stem}.jpg"

        # Determine document type if not provided
        if document_type is None:
            document_type = self._infer_document_type(filename, mime_type)
        
        doc = Document(
            id=doc_id,
            filename=filename,
            document_type=document_type,
            status=DocumentStatus.PROCESSING,
            metadata={
                "mime_type": mime_type,
                "file_size": len(file_content),
                "uploaded_at": datetime.utcnow().isoformat()
            }
        )
        
        # Save file
        file_path = self.upload_dir / f"{doc_id}_{filename}"
        file_path.write_bytes(file_content)
        
        # Extract text based on file type
        try:
            if mime_type == "application/pdf":
                raw_text = self._extract_pdf_text(file_path)
            elif mime_type.startswith("text/"):
                raw_text = file_content.decode("utf-8", errors="ignore")
            elif mime_type.startswith("image/"):
                raw_text = self._extract_image_text(file_path)
            else:
                raw_text = "[Unsupported file type]"
            
            doc.raw_text = raw_text
            doc.status = DocumentStatus.PROCESSED
            doc.processed_at = datetime.utcnow()
            
            logger.info(
                "Document processed successfully",
                doc_id=doc_id,
                filename=filename,
                doc_type=document_type.value,
                text_length=len(raw_text)
            )
            
        except Exception as e:
            doc.status = DocumentStatus.ERROR
            doc.metadata["error"] = str(e)
            logger.error(
                "Document processing failed",
                doc_id=doc_id,
                error=str(e)
            )
        
        return doc
    
    def _extract_pdf_text(self, file_path: Path) -> str:
        """Extract text from PDF using pdfplumber (better for tables)."""
        text_parts = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    # Extract text
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(f"--- Page {i + 1} ---\n{page_text}")
                    
                    # Extract tables (common in rate confirmations)
                    tables = page.extract_tables()
                    for j, table in enumerate(tables):
                        text_parts.append(f"\n--- Table {j + 1} ---")
                        for row in table:
                            text_parts.append(" | ".join(str(cell or "") for cell in row))
        
        except Exception as e:
            logger.warning("pdfplumber extraction failed", error=str(e), file=str(file_path))
            raise RuntimeError("PDF text extraction failed") from e

        return "\n\n".join(text_parts)

    def _convert_heic_to_jpeg(self, file_content: bytes) -> bytes | None:
        """Convert HEIC bytes to JPEG if pillow-heif is available."""
        if not (HAS_PIL and HAS_HEIF):
            return None
        try:
            from io import BytesIO

            with Image.open(BytesIO(file_content)) as image:
                rgb_image = image.convert("RGB")
                out = BytesIO()
                rgb_image.save(out, format="JPEG", quality=92)
                return out.getvalue()
        except Exception as exc:
            logger.warning("HEIC conversion failed", error=str(exc))
            return None

    def _extract_image_text(self, file_path: Path) -> str:
        """Extract text from image documents using OCR when available."""
        if not HAS_PIL:
            raise RuntimeError("Image OCR requires Pillow runtime dependency.")
        if not HAS_TESSERACT:
            raise RuntimeError("Image OCR requires pytesseract + tesseract binary.")
        try:
            with Image.open(file_path) as image:
                text = pytesseract.image_to_string(image)
                cleaned = (text or "").strip()
                if cleaned:
                    return cleaned
                return ""
        except Exception as exc:
            logger.warning("Image OCR failed", filename=str(file_path), error=str(exc))
            raise RuntimeError("Image OCR failed") from exc
    
    def _infer_document_type(self, filename: str, mime_type: str) -> DocumentType:
        """Infer document type from filename and content."""
        filename_lower = filename.lower()
        
        # Check filename patterns
        if any(term in filename_lower for term in ["rate", "confirmation", "ratecon"]):
            return DocumentType.RATE_CONFIRMATION
        elif any(term in filename_lower for term in ["invoice", "inv"]):
            return DocumentType.INVOICE
        elif any(term in filename_lower for term in ["pod", "delivery", "proof"]):
            return DocumentType.POD
        elif any(term in filename_lower for term in ["bol", "bill of lading"]):
            return DocumentType.BOL
        elif any(term in filename_lower for term in ["lumper"]):
            return DocumentType.LUMPER_RECEIPT
        elif any(term in filename_lower for term in ["routing", "guide"]):
            return DocumentType.ROUTING_GUIDE
        elif mime_type == "message/rfc822" or filename_lower.endswith(".eml"):
            return DocumentType.EMAIL
        
        return self.MIME_TYPES.get(mime_type, DocumentType.OTHER)
    
    def chunk_text(
        self, 
        text: str, 
        chunk_size: int = 1000, 
        chunk_overlap: int = 200
    ) -> list[tuple[str, dict]]:
        """Split text into overlapping chunks with metadata."""
        chunks = []
        start = 0
        
        while start < len(text):
            end = min(start + chunk_size, len(text))
            
            # Try to break at newline or space
            if end < len(text):
                # Look for a good break point
                for i in range(min(100, end - start)):
                    if text[end - i] in '\n. ':
                        end -= i
                        break
            
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append((chunk_text, {
                    "char_start": start,
                    "char_end": end,
                    "chunk_index": len(chunks)
                }))

            if end >= len(text):
                break

            # Ensure forward progress even if chunking lands on same boundary.
            start = max(end - chunk_overlap, start + 1)
        
        return chunks


# Singleton instance
document_processor = DocumentProcessor()
