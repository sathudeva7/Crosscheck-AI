"""Conversations, read and written server-side.

Every function here takes a `patient_id` and filters by it. That is not
defensive habit — a session id is a bare UUID that travels in a URL, and the
realistic way one leaks is a pasted link, not a guess. Scoping the query means a
leaked id is inert against anyone else's account.

Nothing in this module decides anything about an answer. It stores what the
agent produced and hands the earlier turns back on the next question.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import QaMessage, QaSession

logger = logging.getLogger(__name__)

# How many earlier messages are replayed to the model. Ten exchanges is well
# past where a follow-up still refers to something — 'and the one before that?'
# reaches back two, not twenty — and every extra turn is tokens spent on every
# question thereafter.
REPLAY_LIMIT = 20

# How many conversations the list returns. The sidebar is a way back to
# something recent, not an archive.
SESSION_LIMIT = 50

TITLE_CHARS = 60


def title_from(question: str) -> str:
    """A conversation's name: its opening question, shortened.

    Deliberately not model-written. A generated title costs a call and a delay
    on the one turn where the user is already waiting longest, to produce
    something nobody reads twice.
    """
    text = " ".join(question.split())
    if len(text) <= TITLE_CHARS:
        return text or "New conversation"
    # Cut at a word rather than mid-syllable, unless the first word is itself
    # longer than the limit.
    clipped = text[:TITLE_CHARS].rsplit(" ", 1)[0] or text[:TITLE_CHARS]
    return f"{clipped}…"


# --- reading -----------------------------------------------------------------


def get_session(db: Session, patient_id: UUID, session_id: UUID) -> QaSession | None:
    """One conversation, if it belongs to this patient."""
    return db.execute(
        select(QaSession).where(
            QaSession.id == session_id,
            QaSession.patient_id == patient_id,
        )
    ).scalar_one_or_none()


def list_sessions(db: Session, patient_id: UUID, limit: int = SESSION_LIMIT) -> list[QaSession]:
    """This patient's conversations, most recently used first."""
    return list(
        db.execute(
            select(QaSession)
            .where(QaSession.patient_id == patient_id)
            .order_by(QaSession.updated_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def messages(db: Session, session_id: UUID, limit: int | None = None) -> list[QaMessage]:
    """A conversation's messages, oldest first.

    `limit` takes the *last* n and returns them still in order, which is what
    replay wants: the recent end of a long conversation, read forwards.
    """
    query = select(QaMessage).where(QaMessage.session_id == session_id)
    if limit is None:
        return list(db.execute(query.order_by(QaMessage.created_at)).scalars().all())

    recent = db.execute(
        query.order_by(QaMessage.created_at.desc(), QaMessage.id.desc()).limit(limit)
    ).scalars()
    return list(reversed(list(recent)))


def replay(rows: list[QaMessage]) -> list[AnyMessage]:
    """Earlier turns, in the form the graph expects.

    Only the text crosses over. The citations and confidence stored alongside
    an answer are for the reader, and feeding them back would invite the model
    to treat its own past summary as a source — the facts have to come from a
    tool on this turn or they do not count.

    A refused turn does not cross over at all — neither the refusal nor the
    message that earned it. Without that, blocking is theatre: the guard reads
    one question at a time, so an attempt refused at 10:00 would be replayed
    into the agent's context at 10:01 as established conversation, arriving
    behind the guard rather than in front of it. Ask the injection, get refused,
    then ask 'what did I say earlier?' and it lands anyway. The refusal itself
    is dropped for a smaller reason: it is the only assistant turn in the
    transcript that no tool backs, and a model shown its own unexplained refusal
    tends to repeat it.

    The transcript keeps both. This governs what the *model* is shown, not what
    the reader sees — a blocked turn stays visible on the screen it happened on.
    """
    kept: list[QaMessage] = []
    for row in rows:
        if row.role == "assistant" and row.blocked:
            # The provoking question is the user row immediately before. One,
            # not every trailing one: a question whose answer failed outright
            # also leaves an unpaired user row, and that one was never judged
            # unsafe.
            if kept and kept[-1].role == "user":
                kept.pop()
            continue
        kept.append(row)

    return [
        HumanMessage(row.content) if row.role == "user" else AIMessage(row.content)
        for row in kept
        if row.content
    ]


def history_for(db: Session, session_id: UUID) -> list[AnyMessage]:
    """The recent end of a conversation, ready to replay."""
    return replay(messages(db, session_id, limit=REPLAY_LIMIT))


# --- writing -----------------------------------------------------------------


def open_session(db: Session, patient_id: UUID, question: str) -> QaSession:
    """Start a conversation, named after the question that started it."""
    session = QaSession(patient_id=patient_id, title=title_from(question))
    db.add(session)
    db.flush()
    return session


def resolve_session(
    db: Session, patient_id: UUID, session_id: UUID | None, question: str
) -> QaSession | None:
    """The conversation to answer into, creating one if none was named.

    Returns None when an id was given that this patient does not own — the
    caller turns that into a 404. Creating a fresh conversation instead would
    be friendlier and wrong: it would quietly answer into a different place
    than the one asked for.
    """
    if session_id is None:
        return open_session(db, patient_id, question)
    return get_session(db, patient_id, session_id)


def add_question(db: Session, session_id: UUID, question: str) -> QaMessage:
    """Record the question before answering it.

    Written first on purpose. If the answer fails or the reader disconnects,
    the question still shows in the transcript — a conversation that visibly
    went unanswered is easier to make sense of than one where the question
    vanished too.
    """
    row = QaMessage(session_id=session_id, role="user", content=question)
    db.add(row)
    db.flush()
    return row


def add_answer(
    db: Session,
    session_id: UUID,
    result: dict[str, Any],
    *,
    truncated: bool = False,
) -> QaMessage:
    """Record an answer with everything that qualified it, and what it cost."""
    usage = result.get("usage") or {}
    row = QaMessage(
        session_id=session_id,
        role="assistant",
        content=result.get("answer") or "",
        confidence=result.get("confidence"),
        band=result.get("band") or "none",
        basis=result.get("basis"),
        grounded=result.get("grounded", True),
        unsupported=result.get("unsupported") or None,
        citations=_jsonable(result.get("citations") or []),
        web=_jsonable(result.get("web") or []),
        tool_calls=list(result.get("tool_calls") or []),
        truncated=truncated,
        blocked=result.get("blocked", False),
        block_reason=result.get("block_reason"),
        # Kept even on a truncated answer. The tokens were spent whether or not
        # the reader stayed to read them, and a bill that only counts finished
        # answers understates what the month actually cost.
        cost_model=usage.get("model"),
        cost_calls=usage.get("calls"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cost_usd=usage.get("cost_usd"),
        cost_by_node=usage.get("by_node") or None,
    )
    db.add(row)
    db.flush()
    return row


def touch(db: Session, session_id: UUID) -> None:
    """Move a conversation to the top of the list.

    An explicit write rather than a reliance on the updated_at trigger: the
    trigger only fires when the session row itself is updated, and adding a
    message does not touch it.
    """
    db.execute(
        update(QaSession).where(QaSession.id == session_id).values(updated_at=func.now())
    )


def rename_session(db: Session, patient_id: UUID, session_id: UUID, title: str) -> QaSession | None:
    session = get_session(db, patient_id, session_id)
    if session is None:
        return None
    session.title = " ".join(title.split())
    db.flush()
    return session


def delete_session(db: Session, patient_id: UUID, session_id: UUID) -> bool:
    """Remove a conversation and its messages. True if there was one to remove."""
    result = db.execute(
        delete(QaSession).where(
            QaSession.id == session_id,
            QaSession.patient_id == patient_id,
        )
    )
    return bool(result.rowcount)


def _jsonable(citations: list[Any]) -> list[dict[str, Any]]:
    """Citations carry UUIDs, which JSONB will not take as they are."""
    return [
        {key: (str(value) if isinstance(value, UUID) else value) for key, value in item.items()}
        for item in citations
        if isinstance(item, dict)
    ]
