"""Asking questions about the record.

    from app.routers.qa_router import router as qa_router
    app.include_router(qa_router)

One endpoint. The agent is built per request, bound to the patient resolved
from the token — never from anything in the request body — so a question can
only ever reach one person's documents.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from sqlalchemy.orm import Session

from app.auth import current_patient
from app.config import QA_TIMEOUT_SECONDS
from app.db.models import Patient
from app.db.session import get_db
from app.rag.agent import ask, astream_answer
from app.schemas.qa import AskRequest, AskResponse, Turn, from_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qa", tags=["qa"])


def _replay(history: list[Turn]) -> list[AnyMessage]:
    """Earlier turns, in the form the graph expects."""
    return [
        HumanMessage(turn.content) if turn.role == "user" else AIMessage(turn.content)
        for turn in history
    ]


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
    try:
        # Bounded, because there is a browser waiting. Six rounds of tool calls
        # across three attempts is normally seconds, but a model that loops
        # would otherwise hold the connection until something upstream gives
        # up — and an unexplained hang is a worse failure than a stated one.
        result = await asyncio.wait_for(
            ask(db, patient.id, body.question, _replay(body.history)),
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

    return from_agent(result)


def _event(payload: dict) -> str:
    """One server-sent event. The blank line is what ends it — without it the
    browser buffers indefinitely and the stream looks hung."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


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
    """

    async def events():
        try:
            # The timeout wraps the whole stream, not each event: a model that
            # stops mid-sentence should end the request, not hold the
            # connection open forever emitting nothing.
            async with asyncio.timeout(QA_TIMEOUT_SECONDS):
                async for event in astream_answer(db, patient.id, body.question, _replay(body.history)):
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
