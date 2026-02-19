"""Persistent registry for processed documents and operational identifiers."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.logging import logger
from app.models.document import Document, DocumentType


class DocumentRegistry:
    """A lightweight JSON-backed registry used by workflow automation."""

    LOAD_ID_PATTERN = re.compile(r"\bLOAD[-_ ]?(\d{3,}[A-Z0-9]*)\b", re.IGNORECASE)
    PRO_PATTERN = re.compile(r"\bPRO[-_ ]?(\d{4,}[A-Z0-9]*)\b", re.IGNORECASE)
    BOL_PATTERN = re.compile(r"\bBOL[-_ ]?(\d{4,}[A-Z0-9]*)\b", re.IGNORECASE)
    RATE_CONF_PATTERN = re.compile(r"\bRC[-_ ]?([0-9]{6,})\b", re.IGNORECASE)

    def __init__(self) -> None:
        settings = get_settings()
        self._path = Path(settings.document_registry_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._state: Dict[str, Dict[str, Any]] = {"documents": {}}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict) and "documents" in payload:
                self._state = payload
        except Exception as exc:
            logger.warning(
                "Failed to load document registry; starting empty",
                path=str(self._path),
                error=str(exc),
            )

    def _save(self) -> None:
        tmp_path = self._path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(self._state, handle, indent=2, ensure_ascii=True)
        tmp_path.replace(self._path)

    @staticmethod
    def _normalize_identifier(value: str) -> str:
        normalized = value.upper().replace(" ", "").replace("_", "").replace("-", "")
        return normalized

    def _extract_ids(self, document: Document) -> Dict[str, List[str]]:
        extracted = document.extracted_data or {}
        text = document.raw_text or ""

        load_ids = []
        pro_numbers = []
        bol_numbers = []
        rate_conf_numbers = []

        for key in ("load_number", "load_id"):
            value = extracted.get(key)
            if isinstance(value, str) and value.strip():
                load_ids.append(value.strip().upper())

        for key in ("pro_number", "pro"):
            value = extracted.get(key)
            if isinstance(value, str) and value.strip():
                pro_numbers.append(value.strip().upper())

        for key in ("bol_number", "bol"):
            value = extracted.get(key)
            if isinstance(value, str) and value.strip():
                bol_numbers.append(value.strip().upper())

        for key in ("rate_conf_number", "rate_confirmation_number", "confirmation_number"):
            value = extracted.get(key)
            if isinstance(value, str) and value.strip():
                rate_conf_numbers.append(value.strip().upper())

        for match in self.LOAD_ID_PATTERN.finditer(text):
            load_ids.append(f"LOAD{self._normalize_identifier(match.group(1))}")
        for match in self.PRO_PATTERN.finditer(text):
            pro_numbers.append(f"PRO{self._normalize_identifier(match.group(1))}")
        for match in self.BOL_PATTERN.finditer(text):
            bol_numbers.append(f"BOL{self._normalize_identifier(match.group(1))}")
        for match in self.RATE_CONF_PATTERN.finditer(text):
            rate_conf_numbers.append(f"RC{self._normalize_identifier(match.group(1))}")

        def uniq(values: List[str]) -> List[str]:
            seen = set()
            ordered = []
            for value in values:
                cleaned = value.strip().upper()
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    ordered.append(cleaned)
            return ordered

        return {
            "load_ids": uniq(load_ids),
            "pro_numbers": uniq(pro_numbers),
            "bol_numbers": uniq(bol_numbers),
            "rate_conf_numbers": uniq(rate_conf_numbers),
        }

    def upsert(self, document: Document, tenant_id: str = "demo") -> Dict[str, Any]:
        """Persist or update a document record."""
        identifiers = self._extract_ids(document)
        record = {
            "id": document.id,
            "tenant_id": tenant_id,
            "filename": document.filename,
            "document_type": document.document_type.value,
            "status": document.status.value,
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "processed_at": document.processed_at.isoformat() if document.processed_at else None,
            "metadata": document.metadata or {},
            "extracted_data": document.extracted_data or {},
            "raw_text": (document.raw_text or "")[:120000],
            **identifiers,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        with self._lock:
            self._state.setdefault("documents", {})
            self._state["documents"][document.id] = record
            self._save()

        logger.info(
            "Document added to registry",
            document_id=document.id,
            tenant_id=tenant_id,
            document_type=document.document_type.value,
            load_ids=len(record["load_ids"]),
        )
        return record

    def get(self, document_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        record = self._state.get("documents", {}).get(document_id)
        if not record:
            return None
        if tenant_id is not None and record.get("tenant_id") != tenant_id:
            return None
        return record

    def list(
        self,
        tenant_id: str = "demo",
        document_type: Optional[DocumentType] = None,
        load_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        docs = list(self._state.get("documents", {}).values())
        docs = [doc for doc in docs if doc.get("tenant_id", "demo") == tenant_id]

        if document_type:
            docs = [doc for doc in docs if doc.get("document_type") == document_type.value]

        if load_id:
            normalized = self._normalize_identifier(load_id)
            docs = [
                doc for doc in docs
                if any(self._normalize_identifier(candidate) == normalized for candidate in doc.get("load_ids", []))
            ]

        docs.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return docs[: max(1, min(limit, 1000))]

    def find_related(self, load_id: str, tenant_id: str = "demo") -> List[Dict[str, Any]]:
        """Return all documents that appear related to a load."""
        normalized = self._normalize_identifier(load_id)
        results = []
        for doc in self._state.get("documents", {}).values():
            if doc.get("tenant_id", "demo") != tenant_id:
                continue
            candidates = doc.get("load_ids", [])
            if any(self._normalize_identifier(candidate) == normalized for candidate in candidates):
                results.append(doc)
                continue
            text = doc.get("raw_text", "")
            if normalized in self._normalize_identifier(text):
                results.append(doc)
        results.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return results

    def find_by_identifier(
        self,
        identifier: str,
        tenant_id: str = "demo",
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return documents matching an operational identifier.

        Supported fields include: load_ids, pro_numbers, bol_numbers, rate_conf_numbers.
        """
        normalized = self._normalize_identifier(identifier)
        fields = fields or ["load_ids", "pro_numbers", "bol_numbers", "rate_conf_numbers"]
        results = []

        for doc in self._state.get("documents", {}).values():
            if doc.get("tenant_id", "demo") != tenant_id:
                continue

            matched = False
            for field in fields:
                candidates = doc.get(field, []) or []
                if any(self._normalize_identifier(str(candidate)) == normalized for candidate in candidates):
                    matched = True
                    break

            if matched:
                results.append(doc)
                continue

            text = doc.get("raw_text", "")
            if normalized in self._normalize_identifier(text):
                results.append(doc)

        results.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return results

    def delete(self, document_id: str, tenant_id: Optional[str] = None) -> None:
        with self._lock:
            record = self._state.get("documents", {}).get(document_id)
            if record and (tenant_id is None or record.get("tenant_id") == tenant_id):
                del self._state["documents"][document_id]
                self._save()

    def get_stats(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        documents = list(self._state.get("documents", {}).values())
        if tenant_id is not None:
            documents = [doc for doc in documents if doc.get("tenant_id", "demo") == tenant_id]
        by_type = Counter(doc.get("document_type", "other") for doc in documents)
        unique_loads = set()
        for doc in documents:
            for load_id in doc.get("load_ids", []):
                unique_loads.add(load_id)
        return {
            "total_documents": len(documents),
            "by_type": dict(by_type),
            "unique_load_ids": len(unique_loads),
        }


document_registry = DocumentRegistry()
