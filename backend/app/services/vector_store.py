"""File-backed vector index for document retrieval."""
from __future__ import annotations

from collections import deque
import json
import re
import time
from pathlib import Path
from threading import Lock
from typing import Any, List, Optional

import numpy as np

from app.core.config import get_settings
from app.core.logging import logger
from app.models.document import Document, DocumentType


class VectorStore:
    """Persistent vector index with a vectorized similarity kernel."""

    def __init__(self):
        self.settings = get_settings()
        self.collection_name = self._build_collection_name(self.settings.embedding_model)
        self._path = Path(self.settings.vector_index_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._rows: list[dict] = []
        self._matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self._normalized_matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self._metadata_columns: dict[str, np.ndarray] = {}
        self._embedding_dim: int = 0
        self._metrics_lock = Lock()
        self._search_latency_ms: deque[float] = deque(maxlen=1000)
        self._search_candidate_counts: deque[int] = deque(maxlen=1000)
        self._load()
        self._rebuild_kernel_index()

        logger.info(
            "Vector store initialized",
            backend="jsonl_index",
            index_path=str(self._path),
            collection=self.collection_name,
            chunk_count=len(self._rows),
        )

    @staticmethod
    def _build_collection_name(embedding_model: str) -> str:
        safe_model = re.sub(r"[^a-z0-9]+", "_", embedding_model.lower()).strip("_")
        safe_model = safe_model[:20] or "default"
        return f"trucking_docs_{safe_model}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        rows: list[dict] = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
        except Exception as exc:
            logger.warning("Failed to load vector index", path=str(self._path), error=str(exc))
            rows = []
        self._rows = rows

    def _persist(self) -> None:
        lines = [json.dumps(row, ensure_ascii=True) for row in self._rows]
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        tmp.replace(self._path)

    @staticmethod
    def _to_vector(value: List[float]) -> np.ndarray:
        arr = np.asarray(value, dtype=np.float32)
        if arr.ndim != 1:
            return np.zeros((0,), dtype=np.float32)
        return arr

    def _rebuild_kernel_index(self) -> None:
        if not self._rows:
            self._matrix = np.zeros((0, 0), dtype=np.float32)
            self._normalized_matrix = np.zeros((0, 0), dtype=np.float32)
            self._metadata_columns = {}
            self._embedding_dim = 0
            return

        dim = 0
        for row in self._rows:
            vec = self._to_vector(row.get("embedding", []))
            if vec.size > 0:
                dim = int(vec.size)
                break

        if dim <= 0:
            self._matrix = np.zeros((len(self._rows), 0), dtype=np.float32)
            self._normalized_matrix = np.zeros((len(self._rows), 0), dtype=np.float32)
            self._metadata_columns = {}
            self._embedding_dim = 0
            return

        matrix = np.zeros((len(self._rows), dim), dtype=np.float32)
        for idx, row in enumerate(self._rows):
            vec = self._to_vector(row.get("embedding", []))
            if vec.size != dim:
                continue
            matrix[idx] = vec

        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        safe_norms = np.where(norms > 0, norms, 1.0).astype(np.float32)
        normalized = (matrix / safe_norms).astype(np.float32)
        zero_norm_rows = np.flatnonzero((norms.reshape(-1) <= 0).astype(bool))
        if zero_norm_rows.size > 0:
            normalized[zero_norm_rows] = 0.0

        metadata_keys: set[str] = set()
        for row in self._rows:
            meta = row.get("metadata", {}) or {}
            for key, value in meta.items():
                if self._is_scalar_filter_value(value):
                    metadata_keys.add(str(key))

        metadata_columns: dict[str, np.ndarray] = {}
        row_count = len(self._rows)
        for key in metadata_keys:
            col = np.empty((row_count,), dtype=object)
            col[:] = None
            for idx, row in enumerate(self._rows):
                value = (row.get("metadata", {}) or {}).get(key)
                if self._is_scalar_filter_value(value):
                    col[idx] = value
            metadata_columns[key] = col

        self._matrix = matrix
        self._normalized_matrix = normalized
        self._metadata_columns = metadata_columns
        self._embedding_dim = dim

    @staticmethod
    def _is_scalar_filter_value(value: Any) -> bool:
        return isinstance(value, (str, int, float, bool))

    def _record_search_metric(self, latency_ms: float, candidates: int) -> None:
        with self._metrics_lock:
            self._search_latency_ms.append(max(0.0, float(latency_ms)))
            self._search_candidate_counts.append(max(0, int(candidates)))

    def _search_metrics(self) -> dict:
        with self._metrics_lock:
            latencies = list(self._search_latency_ms)
            candidates = list(self._search_candidate_counts)

        if not latencies:
            return {"samples": 0}

        latencies.sort()
        count = len(latencies)
        p50_idx = min(count - 1, int(round((count - 1) * 0.50)))
        p95_idx = min(count - 1, int(round((count - 1) * 0.95)))
        return {
            "samples": count,
            "avg_ms": round(sum(latencies) / count, 3),
            "p50_ms": round(latencies[p50_idx], 3),
            "p95_ms": round(latencies[p95_idx], 3),
            "avg_candidates": round(sum(candidates) / max(1, len(candidates)), 2),
        }

    async def add_document_chunks(
        self,
        document: Document,
        chunks: List[tuple[str, dict]],
        embeddings: List[List[float]],
        tenant_id: str = "demo",
    ) -> None:
        rows_to_add: list[dict] = []
        for i, ((chunk_text, chunk_meta), embedding) in enumerate(zip(chunks, embeddings)):
            meta = {
                "document_id": document.id,
                "tenant_id": tenant_id,
                "document_type": document.document_type.value,
                "filename": document.filename,
                "chunk_index": chunk_meta.get("chunk_index", i),
                "char_start": chunk_meta.get("char_start", 0),
                "char_end": chunk_meta.get("char_end", 0),
            }
            if document.extracted_data:
                for key, value in document.extracted_data.items():
                    if isinstance(value, (str, int, float, bool)):
                        meta[f"extracted_{key}"] = value

            rows_to_add.append(
                {
                    "chunk_id": f"{document.id}_chunk_{i}",
                    "text": chunk_text,
                    "embedding": embedding,
                    "metadata": meta,
                }
            )

        with self._lock:
            # Remove stale chunks for the same document before adding new ones.
            self._rows = [
                row for row in self._rows
                if not (
                    row.get("metadata", {}).get("document_id") == document.id
                    and row.get("metadata", {}).get("tenant_id") == tenant_id
                )
            ]
            self._rows.extend(rows_to_add)
            self._persist()
            self._rebuild_kernel_index()

        logger.info(
            "Added document chunks to vector store",
            backend="jsonl_index",
            document_id=document.id,
            tenant_id=tenant_id,
            chunk_count=len(rows_to_add),
        )

    async def add_documents_bulk(
        self,
        payload: List[tuple[Document, List[tuple[str, dict]], List[List[float]]]],
        tenant_id: str = "demo",
    ) -> int:
        """Add many documents in one persist/rebuild pass for faster demo seeding."""
        if not payload:
            return 0

        rows_to_add: list[dict] = []
        document_ids: set[str] = set()
        for document, chunks, embeddings in payload:
            document_ids.add(document.id)
            for i, ((chunk_text, chunk_meta), embedding) in enumerate(zip(chunks, embeddings)):
                meta = {
                    "document_id": document.id,
                    "tenant_id": tenant_id,
                    "document_type": document.document_type.value,
                    "filename": document.filename,
                    "chunk_index": chunk_meta.get("chunk_index", i),
                    "char_start": chunk_meta.get("char_start", 0),
                    "char_end": chunk_meta.get("char_end", 0),
                }
                if document.extracted_data:
                    for key, value in document.extracted_data.items():
                        if isinstance(value, (str, int, float, bool)):
                            meta[f"extracted_{key}"] = value

                rows_to_add.append(
                    {
                        "chunk_id": f"{document.id}_chunk_{i}",
                        "text": chunk_text,
                        "embedding": embedding,
                        "metadata": meta,
                    }
                )

        with self._lock:
            self._rows = [
                row
                for row in self._rows
                if not (
                    row.get("metadata", {}).get("tenant_id") == tenant_id
                    and row.get("metadata", {}).get("document_id") in document_ids
                )
            ]
            self._rows.extend(rows_to_add)
            self._persist()
            self._rebuild_kernel_index()

        logger.info(
            "Bulk added document chunks to vector store",
            backend="jsonl_index",
            tenant_id=tenant_id,
            document_count=len(document_ids),
            chunk_count=len(rows_to_add),
        )
        return len(document_ids)

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        tenant_id: Optional[str] = None,
        document_types: Optional[List[DocumentType]] = None,
        filters: Optional[dict] = None,
    ) -> List[dict]:
        start = time.perf_counter()
        allowed_types = set(dt.value for dt in (document_types or []))
        with self._lock:
            rows = self._rows
            normalized_matrix = self._normalized_matrix
            metadata_columns = self._metadata_columns
            dim = self._embedding_dim

        if not rows or normalized_matrix.size == 0 or dim <= 0:
            self._record_search_metric((time.perf_counter() - start) * 1000, 0)
            return []

        query_vec = self._to_vector(query_embedding)
        if query_vec.size != dim:
            self._record_search_metric((time.perf_counter() - start) * 1000, 0)
            return []

        query_norm = float(np.linalg.norm(query_vec))
        if query_norm <= 0:
            self._record_search_metric((time.perf_counter() - start) * 1000, 0)
            return []
        query_vec = (query_vec / query_norm).astype(np.float32)

        row_count = len(rows)
        candidate_mask = np.ones((row_count,), dtype=bool)

        if tenant_id:
            tenant_col = metadata_columns.get("tenant_id")
            if tenant_col is None:
                self._record_search_metric((time.perf_counter() - start) * 1000, 0)
                return []
            candidate_mask &= (tenant_col == tenant_id)

        if allowed_types:
            type_col = metadata_columns.get("document_type")
            if type_col is None:
                self._record_search_metric((time.perf_counter() - start) * 1000, 0)
                return []
            type_mask = np.zeros((row_count,), dtype=bool)
            for doc_type in allowed_types:
                type_mask |= (type_col == doc_type)
            candidate_mask &= type_mask

        if filters:
            for key, value in filters.items():
                if not self._is_scalar_filter_value(value):
                    self._record_search_metric((time.perf_counter() - start) * 1000, 0)
                    return []
                col = metadata_columns.get(str(key))
                if col is None:
                    self._record_search_metric((time.perf_counter() - start) * 1000, 0)
                    return []
                candidate_mask &= (col == value)

        if not np.any(candidate_mask):
            self._record_search_metric((time.perf_counter() - start) * 1000, 0)
            return []

        cand_idx = np.flatnonzero(candidate_mask)
        cand_matrix = normalized_matrix[cand_idx]
        similarities = cand_matrix @ query_vec

        k = max(1, int(top_k))
        if similarities.size > k:
            selected = np.argpartition(-similarities, k - 1)[:k]
            selected = selected[np.argsort(-similarities[selected])]
        else:
            selected = np.argsort(-similarities)

        results: list[dict] = []
        for local_idx in selected:
            row_idx = int(cand_idx[int(local_idx)])
            row = rows[row_idx]
            results.append(
                {
                    "chunk_id": row.get("chunk_id", ""),
                    "text": row.get("text", ""),
                    "metadata": row.get("metadata", {}),
                    "similarity": float(similarities[int(local_idx)]),
                }
            )

        self._record_search_metric((time.perf_counter() - start) * 1000, int(cand_idx.size))
        return results

    async def delete_document(self, document_id: str, tenant_id: Optional[str] = None) -> None:
        with self._lock:
            before = len(self._rows)
            self._rows = [
                row for row in self._rows
                if not (
                    row.get("metadata", {}).get("document_id") == document_id
                    and (tenant_id is None or row.get("metadata", {}).get("tenant_id") == tenant_id)
                )
            ]
            self._persist()
            self._rebuild_kernel_index()

        logger.info(
            "Deleted document from vector store",
            backend="jsonl_index",
            document_id=document_id,
            removed=before - len(self._rows),
        )

    def get_stats(self, tenant_id: Optional[str] = None) -> dict:
        unique_docs = set()
        total_chunks = 0
        for row in self._rows:
            meta = row.get("metadata", {})
            if tenant_id and meta.get("tenant_id") != tenant_id:
                continue
            total_chunks += 1
            doc_id = meta.get("document_id")
            if doc_id:
                unique_docs.add(doc_id)

        return {
            "total_chunks": total_chunks,
            "unique_documents": len(unique_docs),
            "collection_name": self.collection_name,
            "backend": "jsonl_index",
            "kernel": {
                "type": "numpy_cosine_kernel",
                "embedding_dim": self._embedding_dim,
                "metadata_columns": len(self._metadata_columns),
                **self._search_metrics(),
            },
        }


# Singleton instance
vector_store = VectorStore()
