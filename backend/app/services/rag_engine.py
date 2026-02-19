"""RAG (Retrieval-Augmented Generation) engine."""

from __future__ import annotations

import asyncio
import math
import re
import threading
import time
from collections import Counter, deque
from typing import Any

from app.core.config import get_settings
from app.core.logging import logger
from app.models.document import QueryRequest, QueryResponse
from app.services.document_registry import document_registry
from app.services.embeddings import embedding_service
from app.services.vector_store import vector_store

try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except Exception:
    AsyncOpenAI = None
    HAS_OPENAI = False


class RAGEngine:
    """Retrieval-Augmented Generation engine for trucking queries."""

    SYSTEM_PROMPT = """You are an AI assistant for a trucking company. Your job is to help dispatchers, drivers, and operations staff find information quickly and accurately.

You have access to company documents including:
- Rate confirmations (showing load details, rates, broker info)
- Invoices and payment documents
- Proof of Delivery (POD) documents
- Bills of Lading (BOL)
- Lumper receipts
- Company policies and SOPs
- Routing guides and customer requirements

Guidelines:
1. Answer based ONLY on the provided context documents
2. If the answer isn't in the context, say "I don't have that information in the available documents"
3. Cite specific documents by filename when providing information
4. Be concise but thorough - truckers are busy
5. For rate/money questions, be precise with numbers
6. For policy questions, reference the specific policy document

If asked about fraud or suspicious activity, remind users to verify:
- Broker MC numbers against FMCSA
- Email domains match the broker's official domain
- Rate confirmations match agreed terms

Always prioritize safety and compliance in your answers."""

    LOAD_ID_PATTERN = re.compile(r"(LOAD[0-9A-Z-]{3,})", re.IGNORECASE)
    BOL_ID_PATTERN = re.compile(r"(BOL[0-9A-Z-]{3,})", re.IGNORECASE)

    def __init__(self):
        self.settings = get_settings()
        self.model = self.settings.llm_model
        self._provider = "unconfigured"

        self._tinker_sampling_client = None
        self._tinker_tokenizer = None
        self._tinker_model_input_cls = None
        self._tinker_sampling_params_cls = None
        self._cache_ttl_seconds = max(1, int(self.settings.rag_cache_ttl_seconds))
        self._response_cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._latency_samples_ms = deque(maxlen=max(10, int(self.settings.rag_metrics_window_size)))
        self._route_counters: Counter[str] = Counter()

        self._initialize_tinker_provider()
        if self._provider != "tinker":
            self._initialize_openai_provider()

    def _initialize_tinker_provider(self) -> None:
        tinker_model_path = (self.settings.tinker_model_path or "").strip()
        if not tinker_model_path:
            return

        try:
            from tinker import ModelInput, SamplingParams, ServiceClient

            service_client = ServiceClient(user_metadata={"project": "shams_trucking_sft"})
            self._tinker_sampling_client = service_client.create_sampling_client(model_path=tinker_model_path)
            self._tinker_tokenizer = self._tinker_sampling_client.get_tokenizer()
            self._tinker_model_input_cls = ModelInput
            self._tinker_sampling_params_cls = SamplingParams
            self._provider = "tinker"
            self.model = tinker_model_path
            logger.info("Using Tinker sampling provider for RAG", model_path=tinker_model_path)
            threading.Thread(target=self._warmup_tinker, daemon=True).start()
        except Exception as exc:
            logger.warning("Failed to initialize Tinker provider", error=str(exc))

    def _initialize_openai_provider(self) -> None:
        api_key = self.settings.resolved_openai_api_key()
        if api_key is not None and HAS_OPENAI:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=self.settings.openai_base_url,
            )
            self._provider = "openai"
            logger.info("Using OpenAI-compatible provider for RAG", model=self.model)
            return

        self._provider = "unconfigured"
        logger.warning(
            "RAG generation provider is not configured; set TINKER_MODEL_PATH or OPENAI_BASE_URL+OPENAI_API_KEY"
        )

    async def query(
        self,
        request: QueryRequest,
        tenant_id: str = "demo",
        extra_context: str | None = None,
    ) -> QueryResponse:
        """Execute a RAG query."""
        start_time = time.time()

        try:
            # Include extra_context in cache key if present
            cache_key = self._cache_key(f"{request.query}|{extra_context or ''}", tenant_id, request.document_types)
            cached = self._cache_get(cache_key)
            if cached:
                processing_time = (time.time() - start_time) * 1000
                self._record_query_metric("cache_hit", processing_time, success=True)
                return QueryResponse(
                    answer=cached["answer"],
                    sources=cached["sources"] if request.include_sources else [],
                    confidence=cached["confidence"],
                    processing_time_ms=processing_time,
                )

            routed_response = self._try_structured_answer(request.query, tenant_id, start_time)
            if routed_response is not None:
                self._record_query_metric("structured", routed_response.processing_time_ms, success=True)
                return routed_response

            query_embedding = await embedding_service.embed_text(request.query)
            top_k = max(1, min(request.top_k, self.settings.rag_max_context_chunks))
            retrieved_chunks = await vector_store.search(
                query_embedding=query_embedding,
                top_k=top_k,
                tenant_id=tenant_id,
                document_types=request.document_types,
            )

            if not retrieved_chunks and not extra_context:
                processing_time = (time.time() - start_time) * 1000
                self._record_query_metric("no_retrieval", processing_time, success=False)
                return QueryResponse(
                    answer="I couldn't find any relevant documents to answer your question. Try uploading related documents or rephrasing your query.",
                    sources=[],
                    confidence=0.0,
                    processing_time_ms=processing_time,
                )

            context, sources = self._build_context(retrieved_chunks)
            if extra_context:
                context = f"SYSTEM STATE:\n{extra_context}\n\n---\n\n{context}"

            if not context:
                processing_time = (time.time() - start_time) * 1000
                self._record_query_metric("empty_context", processing_time, success=False)
                return QueryResponse(
                    answer="I found documents, but could not extract enough text to answer. Try a more specific question.",
                    sources=sources if request.include_sources else [],
                    confidence=0.25,
                    processing_time_ms=processing_time,
                )

            route = "llm_generation"
            answer = await self._generate_answer(request.query, context, sources)

            if self._provider == "tinker":
                answer = self._sanitize_tinker_answer(answer)

            avg_similarity = sum(s["similarity"] for s in sources) / len(sources)
            confidence = min(avg_similarity * 1.2, 0.95)
            processing_time = (time.time() - start_time) * 1000
            self._record_query_metric(route, processing_time, success=True)

            logger.info(
                "RAG query completed",
                query=request.query[:50] + "..." if len(request.query) > 50 else request.query,
                provider=self._provider,
                chunks_retrieved=len(retrieved_chunks),
                chunks_used=len(sources),
                confidence=confidence,
                processing_time_ms=processing_time,
            )

            self._cache_set(
                cache_key,
                answer=answer,
                sources=sources,
                confidence=confidence,
            )

            return QueryResponse(
                answer=answer,
                sources=sources if request.include_sources else [],
                confidence=confidence,
                processing_time_ms=processing_time,
            )

        except Exception as exc:
            self._record_query_metric("error", (time.time() - start_time) * 1000, success=False)
            logger.error("RAG query failed", error=str(exc))
            raise

    def _build_context(self, retrieved_chunks: list[dict]) -> tuple[str, list[dict]]:
        """Build a bounded context to keep inference latency predictable."""
        context_parts: list[str] = []
        sources: list[dict] = []
        total_chars = 0
        chunk_limit = max(200, self.settings.rag_chunk_char_limit)
        context_limit = max(1000, self.settings.rag_context_char_limit)

        for chunk in retrieved_chunks:
            metadata = chunk.get("metadata", {}) or {}
            filename = metadata.get("filename", "unknown")
            text = (chunk.get("text") or "").strip()
            if not text:
                continue

            trimmed = text[:chunk_limit]
            doc_number = len(context_parts) + 1
            part = f"[Document {doc_number}: {filename}]\n{trimmed}"
            if context_parts and (total_chars + len(part)) > context_limit:
                break

            context_parts.append(part)
            total_chars += len(part)
            sources.append(
                {
                    "filename": filename,
                    "document_type": metadata.get("document_type", "unknown"),
                    "similarity": chunk.get("similarity", 0.0),
                    "chunk_index": metadata.get("chunk_index", 0),
                }
            )

        return "\n\n---\n\n".join(context_parts), sources

    def _warmup_tinker(self) -> None:
        """Warm tokenizer/session once to reduce first-query latency."""
        try:
            _ = self._sample_with_tinker("Question: ping\nAnswer:", 8, 0.0)
            logger.info("Tinker warmup complete")
        except Exception as exc:
            logger.warning("Tinker warmup failed", error=str(exc))

    def _cache_key(self, query: str, tenant_id: str, document_types: Any) -> str:
        type_key = ",".join(sorted(str(t) for t in (document_types or [])))
        # Include a hash of query to handle potential large extra_context
        import hashlib
        q_hash = hashlib.md5(query.encode("utf-8")).hexdigest()
        return f"{tenant_id}|{type_key}|{q_hash}"

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        with self._cache_lock:
            row = self._response_cache.get(key)
            if not row:
                return None
            if (time.time() - row["ts"]) > self._cache_ttl_seconds:
                self._response_cache.pop(key, None)
                return None
            return row

    def _cache_set(self, key: str, answer: str, sources: list[dict], confidence: float) -> None:
        with self._cache_lock:
            self._response_cache[key] = {
                "answer": answer,
                "sources": sources,
                "confidence": confidence,
                "ts": time.time(),
            }
            # Keep memory bounded under heavy repeated demos.
            if len(self._response_cache) > 2000:
                oldest_key = min(self._response_cache.items(), key=lambda item: item[1].get("ts", 0))[0]
                self._response_cache.pop(oldest_key, None)

    def _record_query_metric(self, route: str, latency_ms: float, success: bool) -> None:
        latency_ms = max(0.0, float(latency_ms))
        with self._metrics_lock:
            self._latency_samples_ms.append(latency_ms)
            self._route_counters[f"route:{route}"] += 1
            self._route_counters[f"success:{'yes' if success else 'no'}"] += 1
            if latency_ms <= (self.settings.rag_generation_timeout_seconds * 1000):
                self._route_counters["latency:within_budget"] += 1
            else:
                self._route_counters["latency:over_budget"] += 1

    def get_latency_metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            samples = list(self._latency_samples_ms)
            counters = dict(self._route_counters)

        if not samples:
            return {
                "status": "empty",
                "samples_window": 0,
                "target_ms": self.settings.rag_generation_timeout_seconds * 1000,
                "routes": counters,
            }

        samples.sort()
        count = len(samples)
        avg_ms = sum(samples) / count
        p50_ms = samples[min(count - 1, int(math.floor((count - 1) * 0.50)))]
        p95_ms = samples[min(count - 1, int(math.ceil((count - 1) * 0.95)))]
        return {
            "status": "ok",
            "samples_window": count,
            "target_ms": self.settings.rag_generation_timeout_seconds * 1000,
            "avg_ms": round(avg_ms, 2),
            "p50_ms": round(p50_ms, 2),
            "p95_ms": round(p95_ms, 2),
            "min_ms": round(samples[0], 2),
            "max_ms": round(samples[-1], 2),
            "routes": counters,
        }

    @staticmethod
    def _sanitize_tinker_answer(answer: str) -> str:
        text = (answer or "").strip()
        if not text:
            return "I don't have that information in the available documents."

        if "Sources:" in text:
            text = text.split("Sources:", 1)[0].strip()

        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"(_[A-Za-z0-9]+)(?:\1)+", r"\1", text)
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) > 1:
            unique_parts = []
            seen = set()
            for part in parts:
                key = re.sub(r"[^a-z0-9]", "", part.lower())
                if not key or key in seen:
                    continue
                seen.add(key)
                unique_parts.append(part)
            if unique_parts:
                text = ", ".join(unique_parts)
        text = re.sub(r"\s+", " ", text).strip()
        return text or "I don't have that information in the available documents."

    async def _generate_answer(self, query: str, context: str, sources: list[dict]) -> str:
        if self._provider == "tinker":
            return await self._generate_with_tinker(query, context)
        if self._provider == "openai":
            return await self._generate_with_openai(query, context)
        raise RuntimeError(
            "No LLM generation provider configured. Set TINKER_MODEL_PATH or OPENAI_BASE_URL+OPENAI_API_KEY."
        )

    async def _generate_with_openai(self, query: str, context: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Context:\n{context}\n\nQuestion: {query}\n\n"
                        "Answer based on the context above. Cite specific documents when possible."
                    ),
                },
            ],
            temperature=self.settings.llm_temperature,
            max_tokens=min(self.settings.llm_max_tokens, self.settings.rag_answer_max_tokens),
        )
        return (response.choices[0].message.content or "").strip()

    async def _generate_with_tinker(self, query: str, context: str) -> str:
        prompt = (
            f"{self.SYSTEM_PROMPT}\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {query}\n\n"
            "Final answer (concise, factual, with cited filenames when possible):"
        )
        return await asyncio.to_thread(
            self._sample_with_tinker,
            prompt,
            min(self.settings.rag_answer_max_tokens, 400),
            self.settings.llm_temperature,
        )

    def _sample_with_tinker(self, prompt: str, max_tokens: int, temperature: float) -> str:
        if not self._tinker_sampling_client:
            raise RuntimeError("Tinker sampling client is not initialized")

        prompt_tokens = self._encode_tinker(prompt)
        sampling_params = self._tinker_sampling_params_cls(
            max_tokens=max(32, max_tokens),
            temperature=max(0.0, temperature),
            top_p=0.95,
            top_k=40,
        )
        response = self._tinker_sampling_client.sample(
            prompt=self._tinker_model_input_cls.from_ints(prompt_tokens),
            num_samples=1,
            sampling_params=sampling_params,
        ).result()

        if not response.sequences:
            return "I don't have that information in the available documents."

        output_tokens = list(response.sequences[0].tokens)
        if len(output_tokens) >= len(prompt_tokens) and output_tokens[: len(prompt_tokens)] == prompt_tokens:
            output_tokens = output_tokens[len(prompt_tokens) :]

        text = self._decode_tinker(output_tokens).strip()
        return text or "I don't have that information in the available documents."

    def _encode_tinker(self, text: str) -> list[int]:
        try:
            return [int(x) for x in self._tinker_tokenizer.encode(text, add_special_tokens=False)]
        except TypeError:
            return [int(x) for x in self._tinker_tokenizer.encode(text)]

    def _decode_tinker(self, tokens: list[int]) -> str:
        try:
            return self._tinker_tokenizer.decode(tokens, skip_special_tokens=True)
        except TypeError:
            return self._tinker_tokenizer.decode(tokens)

    def _try_structured_answer(
        self,
        query: str,
        tenant_id: str,
        start_time: float,
    ) -> QueryResponse | None:
        """Fast path for high-frequency load-specific and BOL-specific questions."""
        query_lower = query.lower()
        asks_ap_facts = any(
            token in query_lower
            for token in ["invoice", "broker", "rate", "rpm", "ap facts", "rate details", "target rate"]
        )
        asks_bol_facts = any(
            token in query_lower
            for token in ["driver", "equipment", "pro", "bill of lading", "weight", "reference"]
        )

        bol_match = self.BOL_ID_PATTERN.search(query)
        if bol_match:
            bol_id = bol_match.group(0).upper()
            bol_docs = document_registry.find_by_identifier(
                bol_id,
                tenant_id=tenant_id,
                fields=["bol_numbers"],
            )
            bol_doc = next((doc for doc in bol_docs if doc.get("document_type") == "bill_of_lading"), None)
            if not bol_doc and bol_docs:
                bol_doc = bol_docs[0]

            if bol_doc:
                extracted = bol_doc.get("extracted_data", {}) or {}
                answer = (
                    f"{bol_id}: load {extracted.get('load_number') or 'unknown'}, "
                    f"pro {extracted.get('pro_number') or 'unknown'}, "
                    f"equipment {extracted.get('equipment_type') or 'unknown'}, "
                    f"driver {extracted.get('driver_name') or 'unknown'}, "
                    f"reference {extracted.get('reference_number') or 'unknown'}, "
                    f"weight {extracted.get('weight') or 'unknown'}."
                )
                return QueryResponse(
                    answer=answer,
                    sources=self._source_list_from_docs([bol_doc]),
                    confidence=0.92,
                    processing_time_ms=(time.time() - start_time) * 1000,
                )

        load_match = self.LOAD_ID_PATTERN.search(query)
        if asks_ap_facts and not load_match:
            return QueryResponse(
                answer="Please include a load ID (example: LOAD00030) so I can return exact broker/invoice/rate details.",
                sources=[],
                confidence=0.95,
                processing_time_ms=(time.time() - start_time) * 1000,
            )

        if not load_match:
            return None

        load_id = load_match.group(0).upper()
        related_docs = document_registry.find_related(load_id, tenant_id=tenant_id)
        if not related_docs:
            return QueryResponse(
                answer=f"I couldn't find documents for load {load_id} in this tenant.",
                sources=[],
                confidence=0.6,
                processing_time_ms=(time.time() - start_time) * 1000,
            )

        def first_doc(doc_type: str) -> dict | None:
            for doc in related_docs:
                if doc.get("document_type") == doc_type:
                    return doc
            return None

        def first_value(doc: dict | None, keys: list[str]) -> Any:
            if not doc:
                return None
            extracted = doc.get("extracted_data", {}) or {}
            for key in keys:
                value = extracted.get(key)
                if value not in (None, "", []):
                    return value
            return None

        rate_doc = first_doc("rate_confirmation")
        invoice_doc = first_doc("invoice")
        bol_doc = first_doc("bill_of_lading")

        if asks_bol_facts and bol_doc:
            bol_extracted = bol_doc.get("extracted_data", {}) or {}
            answer = (
                f"Load {load_id}: driver {bol_extracted.get('driver_name') or 'unknown'}, "
                f"equipment {bol_extracted.get('equipment_type') or 'unknown'}, "
                f"pro {bol_extracted.get('pro_number') or 'unknown'}, "
                f"weight {bol_extracted.get('weight') or 'unknown'}, "
                f"reference {bol_extracted.get('reference_number') or 'unknown'}."
            )
            return QueryResponse(
                answer=answer,
                sources=self._source_list_from_docs([bol_doc]),
                confidence=0.9,
                processing_time_ms=(time.time() - start_time) * 1000,
            )

        if not asks_ap_facts:
            return None

        broker_name = first_value(rate_doc, ["broker_name"]) or first_value(invoice_doc, ["broker_name"])
        invoice_number = first_value(invoice_doc, ["invoice_number"])
        invoice_amount = first_value(invoice_doc, ["total_amount", "amount_due", "invoice_total"])
        total_rate = first_value(rate_doc, ["rate", "total_rate"])
        rate_per_mile = first_value(rate_doc, ["rate_per_mile"])
        rate_conf_number = first_value(
            rate_doc,
            ["rate_conf_number", "rate_confirmation_number", "confirmation_number"],
        )

        if asks_ap_facts:
            if "rate" in query_lower and "invoice" not in query_lower and "broker" not in query_lower:
                answer = (
                    f"Load {load_id}: total rate {self._money(total_rate)}, "
                    f"rate per mile {self._money(rate_per_mile)}, "
                    f"rate confirmation {rate_conf_number or 'unknown'}."
                )
            elif "invoice" in query_lower and "broker" not in query_lower and "rate" not in query_lower:
                answer = f"Load {load_id}: invoice {invoice_number or 'unknown'} for {self._money(invoice_amount)}."
            else:
                answer = (
                    f"Load {load_id}: broker {broker_name or 'unknown'}, "
                    f"invoice {invoice_number or 'unknown'} ({self._money(invoice_amount)}), "
                    f"rate {self._money(total_rate)} at {self._money(rate_per_mile)}/mile."
                )
        else:
            answer = (
                f"Load {load_id}: broker {broker_name or 'unknown'}, "
                f"invoice {invoice_number or 'unknown'} ({self._money(invoice_amount)}), "
                f"rate confirmation {rate_conf_number or 'unknown'}."
            )

        return QueryResponse(
            answer=answer,
            sources=self._source_list_from_docs(related_docs[:5]),
            confidence=0.9,
            processing_time_ms=(time.time() - start_time) * 1000,
        )

    @staticmethod
    def _money(value: Any) -> str:
        if value is None:
            return "unknown"
        try:
            return f"${float(value):,.2f}"
        except Exception:
            return str(value)

    @staticmethod
    def _source_list_from_docs(docs: list[dict]) -> list[dict]:
        return [
            {
                "filename": doc.get("filename"),
                "document_type": doc.get("document_type"),
                "similarity": 1.0,
                "chunk_index": 0,
            }
            for doc in docs
        ]

    async def generate_counter_offer(
        self,
        rate_con_data: dict,
        target_rate: float,
        reasoning: str,
    ) -> str:
        """Generate a counter-offer email for a load."""
        prompt = f"""Generate a professional counter-offer email for the following load:

Load Details:
- Pickup: {rate_con_data.get('pickup_location', 'N/A')} on {rate_con_data.get('pickup_date', 'N/A')}
- Delivery: {rate_con_data.get('delivery_location', 'N/A')} on {rate_con_data.get('delivery_date', 'N/A')}
- Equipment: {rate_con_data.get('equipment_type', 'N/A')}
- Current Rate: ${rate_con_data.get('rate', 'N/A')}
- Target Rate: ${target_rate}
- Miles: {rate_con_data.get('miles', 'N/A')}

Reasoning for counter: {reasoning}

Generate a professional, concise email that:
1. Thanks the broker for the opportunity
2. States the requested rate clearly
3. Provides brief justification (market rates, deadhead, etc.)
4. Maintains positive relationship

Keep it brief - brokers are busy."""

        if self._provider == "tinker":
            return await asyncio.to_thread(self._sample_with_tinker, prompt, 500, 0.3)
        if self._provider != "openai":
            raise RuntimeError(
                "No LLM generation provider configured. Set TINKER_MODEL_PATH or OPENAI_BASE_URL+OPENAI_API_KEY."
            )

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a professional trucking dispatcher writing rate negotiation emails."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        return (response.choices[0].message.content or "").strip()

    def get_runtime_info(self) -> dict[str, Any]:
        return {
            "provider": self._provider,
            "model": self.model,
            "cache_entries": len(self._response_cache),
            "cache_ttl_seconds": self._cache_ttl_seconds,
            "latency_budget_seconds": self.settings.rag_generation_timeout_seconds,
            "max_context_chunks": self.settings.rag_max_context_chunks,
            "max_answer_tokens": self.settings.rag_answer_max_tokens,
            "metrics_window_size": int(self.settings.rag_metrics_window_size),
        }


# Singleton instance
rag_engine = RAGEngine()
