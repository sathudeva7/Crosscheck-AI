"""The CrossCheck API.

Reads uploaded medical documents, merges what they contain into one timeline
per patient, cross-checks that timeline for drug interactions, duplicates,
dose conflicts and lab trends, and answers questions about it.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import storage
from app.config import CORS_ORIGINS
from app.db.migrate import run_migrations
from app.ingest.pipeline import resume_stranded
from app.rag.embedder import build_index
from app.routers.document_router import router as document_router
from app.routers.finding_router import router as finding_router
from app.routers.lab_router import router as lab_router
from app.routers.patient_router import router as patient_router
from app.routers.qa_router import router as qa_router
from app.routers.rag_router import router as rag_router
from app.routers.timeline_router import router as timeline_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bring the schema up to date before anything touches the database
    run_migrations()
    # And make sure there is somewhere to put uploads. A no-op on the local
    # backend and when the bucket already exists, so it costs one request at
    # boot and saves the first upload on a fresh project from failing.
    try:
        storage.ensure_bucket()
    except Exception:  # noqa: BLE001 - storage being down must not stop the API
        logger.exception("could not prepare the storage bucket; uploads may fail")
    # Finish anything a restart interrupted. Deliberately a task rather than an
    # await: a resumed document can take half a minute, and the API must be
    # answering requests long before that. Held in a local so it is not
    # garbage-collected mid-flight, which is a real hazard with bare tasks.
    resuming = asyncio.create_task(resume_stranded())

    # Build the vector index once at startup instead of on the first request
    # build_index()
    yield

    resuming.cancel()


app = FastAPI(
    title="CrossCheck API",
    description=(
        "Cross-checks a patient's own medical documents. Every answer names the "
        "records it was built from and how well they were read. None of it is a "
        "diagnosis."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

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
app.include_router(lab_router)
app.include_router(qa_router)
app.include_router(rag_router)
