"""Example FastAPI entrypoint. If you already have a main.py, just copy the
lifespan + include_router bits into yours."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.db.migrate import run_migrations
from app.rag.embedder import build_index
from app.routers.document_router import router as document_router
from app.routers.finding_router import router as finding_router
from app.routers.patient_router import router as patient_router
from app.routers.qa_router import router as qa_router
from app.routers.rag_router import router as rag_router
from app.routers.timeline_router import router as timeline_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bring the schema up to date before anything touches the database
    run_migrations()
    # Build the vector index once at startup instead of on the first request
    # build_index()
    yield


app = FastAPI(title="Agentic RAG API", lifespan=lifespan)

# The browser refuses every cross-origin call without this. Identity travels in
# the Authorization header rather than a cookie, so credentials stay off: the
# token is attached deliberately by the frontend, never sent automatically.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(patient_router)
app.include_router(document_router)
app.include_router(timeline_router)
app.include_router(finding_router)
app.include_router(qa_router)
app.include_router(rag_router)
