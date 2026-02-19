"""OpenClaw - Trucking AI Copilot API"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.core.logging import configure_logging, logger
from app.routers import documents, rag, negotiation, workflows, integrations, ops, samsara_adapter, agent_os


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "OpenClaw API starting",
        version="0.1.0",
        llm_model=settings.llm_model,
        embedding_model=settings.embedding_model
    )
    yield
    # Shutdown
    logger.info("OpenClaw API shutting down")


app = FastAPI(
    title="OpenClaw API",
    description="AI Copilot for Trucking Operations - Document RAG, Extraction, and Rate Negotiation",
    version="0.1.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(documents.router)
app.include_router(rag.router)
app.include_router(negotiation.router)
app.include_router(workflows.router)
app.include_router(integrations.router)
app.include_router(ops.router)
app.include_router(samsara_adapter.router)
app.include_router(agent_os.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "OpenClaw API",
        "version": "0.1.0",
        "description": "AI Copilot for Trucking Operations",
        "endpoints": {
            "documents": "/documents",
            "rag": "/rag",
            "negotiation": "/negotiation",
            "workflows": "/workflows",
            "integrations": "/integrations",
            "ops": "/ops",
            "agent_os": "/agent-os",
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
