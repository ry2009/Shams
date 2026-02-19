"""Embeddings service for vector search."""
from typing import List
import numpy as np

from app.core.config import get_settings
from app.core.logging import logger

try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except Exception:
    AsyncOpenAI = None
    HAS_OPENAI = False


class EmbeddingService:
    """Service for generating text embeddings."""
    
    def __init__(self):
        self.settings = get_settings()
        api_key = self.settings.resolved_openai_api_key()
        self._enabled = api_key is not None and HAS_OPENAI
        if self._enabled:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=self.settings.openai_base_url
            )
            self.model = self.settings.embedding_model
        else:
            logger.warning(
                "Embedding provider is not configured; set OPENAI_BASE_URL and OPENAI_API_KEY (or local-compatible endpoint)"
            )
        
        self._cache = {}
    
    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        if not self._enabled:
            raise RuntimeError(
                "Embedding provider unavailable. Configure OPENAI_BASE_URL and OPENAI_API_KEY."
            )
        
        # Check cache
        cache_key = hash(text)
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=text
            )
            embedding = response.data[0].embedding
            self._cache[cache_key] = embedding
            return embedding
        except Exception as e:
            logger.error("Embedding generation failed", error=str(e))
            raise
    
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts efficiently."""
        if not self._enabled:
            raise RuntimeError(
                "Embedding provider unavailable. Configure OPENAI_BASE_URL and OPENAI_API_KEY."
            )
        
        # Filter out cached texts
        to_embed = []
        indices = []
        results = [None] * len(texts)
        
        for i, text in enumerate(texts):
            cache_key = hash(text)
            if cache_key in self._cache:
                results[i] = self._cache[cache_key]
            else:
                to_embed.append(text)
                indices.append(i)
        
        if not to_embed:
            return results
        
        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=to_embed
            )
            
            batch_embeddings = [item.embedding for item in response.data]
            
            for idx, embedding in zip(indices, batch_embeddings):
                results[idx] = embedding
                self._cache[hash(texts[idx])] = embedding
            
            return results
        except Exception as e:
            logger.error("Batch embedding failed", error=str(e), batch_size=len(to_embed))
            raise
    
    def cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        a_arr = np.array(a)
        b_arr = np.array(b)
        return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))


# Singleton instance
embedding_service = EmbeddingService()
