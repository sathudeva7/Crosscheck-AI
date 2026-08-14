"""LangGraph Studio entrypoint for the CrossCheck agent.

`langgraph dev` wants one importable graph. `build_agent` cannot be that: it
takes a session and a patient id, and the whole point of building per request
(see the note at the top of `agent.py`) is that a graph compiled once at import
would answer every patient's questions with the first patient's tools.

LangGraph allows a *factory* instead — a callable given the run config, which
returns the graph. That is what this module is: it reads the patient off the
config, opens a session, and hands both to `build_agent`, so Studio gets a
per-run graph on the same terms as an API request.

Development only. The session opened here lives as long as the graph the server
caches for that config, rather than being closed at the end of a request the way
`get_db` closes it in FastAPI. That is tolerable against a dev database and one
operator; it is not how the app serves traffic.

Set the patient once in .env:

    STUDIO_PATIENT_ID=<uuid>

or per run, in Studio's config panel, as `patient_id`. With neither, the most
recently created patient is used — convenient on a dev database with one row in
it, and worth knowing about before you read an answer as being about someone.
"""

from __future__ import annotations

import logging
import os
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from app.db.models import Patient
from app.db.session import SessionLocal
from app.rag.agent import build_agent

logger = logging.getLogger(__name__)


def _patient_id(config: RunnableConfig, db) -> UUID:
    """The patient this run is about: config, then env, then whoever is there."""
    configured = (config.get("configurable") or {}).get("patient_id")
    if configured:
        return UUID(str(configured))

    from_env = os.getenv("STUDIO_PATIENT_ID")
    if from_env:
        return UUID(from_env)

    patient = db.scalars(
        select(Patient).order_by(Patient.created_at.desc()).limit(1)
    ).first()
    if patient is None:
        raise RuntimeError(
            "No patient to answer about. Seed one, or set STUDIO_PATIENT_ID in .env, "
            "or pass patient_id in Studio's config panel."
        )
    logger.warning("no patient_id given, defaulting to %s", patient.id)
    return patient.id


def make_graph(config: RunnableConfig):
    """Compile the agent for the patient named in this run's config."""
    db = SessionLocal()
    return build_agent(db, _patient_id(config, db))
