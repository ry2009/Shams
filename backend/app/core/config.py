"""Application configuration using pydantic-settings."""
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[3] / ".env"),
        case_sensitive=False,
    )
    
    # OpenAI
    openai_api_key: str = ""
    azure_openai_endpoint: str | None = None
    azure_openai_key: str | None = None
    azure_openai_version: str = "2024-02-15-preview"
    
    # Application
    log_level: str = "INFO"
    chroma_db_path: str = "./data/chroma"
    vector_index_path: str = "./data/vector_index.jsonl"
    upload_dir: str = "./uploads"
    document_registry_path: str = "./data/document_registry.json"
    # Leave empty by default; OpsStateStore derives a tenant-scoped sqlite path
    # from OPS_STATE_PATH when explicit OPS_DB_PATH is not provided.
    ops_db_path: str = ""
    ops_state_path: str = "./data/ops_state.json"
    mcleod_export_dir: str = "./data/mcleod_exports"
    max_upload_size: int = 52428800  # 50MB
    auth_enabled: bool = False
    default_tenant_id: str = "demo"
    tenant_tokens: str = ""
    app_mode: str = "demo"
    
    # Vector DB
    embedding_model: str = "text-embedding-3-small"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    top_k_retrieval: int = 5
    
    # LLM
    openai_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 2000
    tinker_model_path: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "z-ai/glm-4.5-air:free"
    openrouter_timeout_seconds: float = 25.0
    free_roam_enabled: bool = True
    free_roam_max_steps: int = 6
    free_roam_memory_turns: int = 12

    # RAG latency/quality controls
    rag_max_context_chunks: int = 2
    rag_chunk_char_limit: int = 420
    rag_context_char_limit: int = 1200
    rag_generation_timeout_seconds: float = 2.5
    rag_answer_max_tokens: int = 100
    rag_cache_ttl_seconds: int = 180
    rag_metrics_window_size: int = 200

    # Microsoft Graph
    ms_graph_tenant_id: str | None = None
    ms_graph_client_id: str | None = None
    ms_graph_client_secret: str | None = None
    ms_graph_user_id: str | None = None
    ms_graph_drive_id: str | None = None

    # Samsara (read-only sync in MVP)
    samsara_api_token: str = ""
    samsara_base_url: str = "https://api.samsara.com"
    samsara_events_url: str = ""

    # Autonomous decision thresholds
    ticket_confidence_threshold: float = 0.985
    ticket_miles_variance_threshold: float = 0.07

    def resolved_openai_api_key(self) -> str | None:
        """
        Resolve API key for OpenAI-compatible clients.

        Local endpoints (e.g. Ollama) often do not require a real key, but the
        OpenAI SDK still expects a non-empty value.
        """
        key = (self.openai_api_key or "").strip()
        if key and key != "sk-your-key-here":
            return key
        if self._is_local_base_url():
            return "local-dev"
        return None

    def _is_local_base_url(self) -> bool:
        if not self.openai_base_url:
            return False
        try:
            host = (urlparse(self.openai_base_url).hostname or "").lower()
        except Exception:
            return False
        return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local")

    def normalized_app_mode(self) -> str:
        mode = (self.app_mode or "").strip().lower()
        return mode if mode in {"demo", "production"} else "production"

    def is_demo_mode(self) -> bool:
        return self.normalized_app_mode() == "demo"
    
@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
