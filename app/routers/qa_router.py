"""Asking questions about the record, and the conversations they belong to.

    from app.routers.qa_router import router as qa_router
    app.include_router(qa_router)

The agent is built per request, bound to the patient resolved from the token —
never from anything in the request body — so a question can only ever reach one
person's documents. The same rule governs conversations: `session_id` arrives
from the client, but every lookup is filtered by the patient in the token, so a
session id that leaks is inert against another account.
"""

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import current_patient
from app.config import QA_TIMEOUT_SECONDS
from app.db.models import Patient, QaSession
from app.db.session import SessionLocal, get_db
from app.rag import history
from app.rag.agent import ask, astream_answer
from app.rag.cost import Usage
from app.schemas.qa import (
    AskRequest,
    AskResponse,
    MessageRead,
    RenameRequest,
    SessionRead,
    TranscriptRead,
    from_agent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qa", tags=["qa"])

NO_SESSION = "No such conversation"


def _resolve(db: Session, patient: Patient, body: AskRequest) -> QaSession:
    """The conversation to answer into, or a 404.

    404 rather than 403 for a session belonging to someone else: the two are
    indistinguishable to the caller, which is the point — a wrong answer here
    would confirm that a given id exists.
    """
    session = history.resolve_session(db, patient.id, body.session_id, body.question)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION)
    return session


# --- the conversations -------------------------------------------------------


@router.get("/sessions", response_model=list[SessionRead])
def list_sessions(
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> list[QaSession]:
    """This patient's conversations, most recently used first."""
    return history.list_sessions(db, patient.id)


@router.get("/sessions/{session_id}", response_model=TranscriptRead)
def read_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> TranscriptRead:
    """One conversation and everything said in it.

    The whole thing, not the replay window: the reader is entitled to scroll
    back further than the model is given to reason over.
    """
    session = history.get_session(db, patient.id, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION)

    return TranscriptRead(
        session=SessionRead.model_validate(session),
        messages=[MessageRead.model_validate(row) for row in history.messages(db, session.id)],
    )


@router.patch("/sessions/{session_id}", response_model=SessionRead)
def rename_session(
    session_id: UUID,
    body: RenameRequest,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> QaSession:
    session = history.rename_session(db, patient.id, session_id, body.title)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION)
    db.commit()
    return session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> Response:
    if not history.delete_session(db, patient.id, session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- asking ------------------------------------------------------------------


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    body: AskRequest,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> AskResponse:
    """Answer a question from the patient's own documents.

    The response always carries a confidence and a disclaimer. Neither is
    decoration: the confidence is the weakest extracted fact the answer stands
    on, so an answer read off a handwritten prescription cannot present itself
    as settled, and `citations` names the rows it came from so the claim can be
    followed back to the page it was read from.
    """
    session = _resolve(db, patient, body)
    replay = history.history_for(db, session.id)

    # Committed before the model is called, so a question that fails to be
    # answered still appears in the transcript rather than disappearing.
    history.add_question(db, session.id, body.question)
    db.commit()

    try:
        # Bounded, because there is a browser waiting. Six rounds of tool calls
        # is normally seconds, but a model that loops would otherwise hold the
        # connection until something upstream gives up — and an unexplained
        # hang is a worse failure than a stated one.
        result = await asyncio.wait_for(
            ask(db, patient.id, body.question, replay),
            timeout=QA_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "That took too long to answer. Try asking about one thing at a time.",
        ) from None
    except Exception as problem:  # noqa: BLE001 - the reason must not reach the browser
        # Logged with the stack, reported without it: the exception text can
        # carry the question, page content and provider detail, none of which
        # belongs in an HTTP body.
        logger.exception("question failed for patient %s", patient.id)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Could not answer that just now. Please try again.",
        ) from problem

    history.add_answer(db, session.id, result)
    history.touch(db, session.id)
    db.commit()

    return from_agent(result, session.id)


def _event(payload: dict) -> str:
    """One server-sent event. The blank line is what ends it — without it the
    browser buffers indefinitely and the stream looks hung."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _salvage(session_id: UUID, answer: str, usage: Usage | None = None) -> None:
    """Keep a half-written answer when the reader disconnects mid-stream.

    On its own connection. By the time this runs the request is being torn
    down and the dependency-injected session is on its way out, so borrowing it
    would be a race — and the whole point is to write something at the moment
    everything else is being abandoned.

    The cost of what was spent before the disconnection is written too. Those
    tokens were bought.
    """
    db = SessionLocal()
    try:
        result: dict = {"answer": answer}
        if usage is not None and usage.calls:
            result["usage"] = usage.as_dict()
        history.add_answer(db, session_id, result, truncated=True)
        history.touch(db, session_id)
        db.commit()
    except Exception:  # noqa: BLE001 - nothing left to report to
        logger.exception("could not save the truncated answer for session %s", session_id)
        db.rollback()
    finally:
        db.close()


@router.post("/stream")
async def ask_streaming(
    body: AskRequest,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> StreamingResponse:
    """The same answer as `/qa/ask`, delivered as it is produced.

    Server-sent events rather than a websocket: the traffic only goes one way,
    and this survives a proxy that would drop an upgrade. Sent over POST rather
    than as an `EventSource`, because the session token belongs in a header and
    `EventSource` cannot set one.

    The final `done` event carries exactly what `/qa/ask` returns, so a client
    that ignores everything before it behaves identically to the plain endpoint.
    A `session` event goes first, ahead of any answer text, so a client that
    started a new conversation can put the id in the address bar immediately —
    a reader who navigates away mid-answer still has somewhere to come back to.
    """
    session = _resolve(db, patient, body)
    replay = history.history_for(db, session.id)

    history.add_question(db, session.id, body.question)
    history.touch(db, session.id)
    db.commit()

    session_id, title = session.id, session.title

    # Owned out here rather than inside the agent so the cost survives the
    # generator being cancelled — see _salvage.
    usage = Usage()

    async def events():
        answer = ""
        finished = False
        try:
            yield _event({"type": "session", "id": str(session_id), "title": title})

            # The timeout wraps the whole stream, not each event: a model that
            # stops mid-sentence should end the request, not hold the
            # connection open forever emitting nothing.
            async with asyncio.timeout(QA_TIMEOUT_SECONDS):
                async for event in astream_answer(
                    db, patient.id, body.question, replay, usage
                ):
                    if event["type"] == "token":
                        answer += event.get("text", "")
                    elif event["type"] == "done":
                        history.add_answer(db, session_id, event)
                        history.touch(db, session_id)
                        db.commit()
                        finished = True
                    yield _event(event)
        except TimeoutError:
            yield _event(
                {
                    "type": "error",
                    "detail": "That took too long to answer. Try asking about one thing at a time.",
                }
            )
        except Exception:  # noqa: BLE001 - the reason must not reach the browser
            # Logged with the stack, reported without it. The status line is
            # already 200 by the time anything fails, so an error here has to
            # travel as an event rather than as a status code.
            logger.exception("streamed question failed for patient %s", patient.id)
            yield _event({"type": "error", "detail": "Could not answer that just now."})
        finally:
            # Reached on cancellation too — closing the tab mid-answer runs
            # this. A partial answer is kept and marked truncated, because a
            # question left visibly half-answered reads better on return than
            # one that appears to have been ignored.
            if not finished and answer.strip():
                _salvage(session_id, answer, usage)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Stops nginx and friends from buffering the whole response, which
            # would defeat the point entirely.
            "X-Accel-Buffering": "no",
        },
    )
