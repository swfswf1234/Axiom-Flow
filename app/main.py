import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.core.database import engine, Base
from app.api.ingest import router as ingest_router
from app.api.status import router as status_router
from app.api.layout import router as layout_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("MINERU_MODEL_SOURCE", "huggingface")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    logger.info("Starting Axiom-Flow v0.1")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")
    yield
    await engine.dispose()
    logger.info("Axiom-Flow shutdown")


app = FastAPI(
    title="Axiom-Flow",
    version="0.1.0",
    description="QED-Engine: Knowledge reconstruction engine (MinerU + LlamaIndex)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router, tags=["Ingest"])
app.include_router(status_router, tags=["Status"])
app.include_router(layout_router, tags=["Layout"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
