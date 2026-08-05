"""Remove every uploaded document and everything read out of it.

    .venv/bin/python -m scripts.clear_documents          # show what would go
    .venv/bin/python -m scripts.clear_documents --apply  # do it

Accounts, logins and conversation history are kept. What goes is the documents,
the pages transcribed from them, the visits and timeline events built out of
them, the safety findings computed over those events, and the stored files.

Irreversible. There is no soft-delete here and no undo — rejecting a timeline
row hides it and keeps it, but this is the other thing entirely. Hence the dry
run being the default and --apply having to be typed.

What cascades and what does not:

    document  ──CASCADE──>  document_page
              ──CASCADE──>  timeline_visit ──CASCADE──> timeline_event

Findings do not. A finding names the events it was computed from in a JSON
array rather than a foreign key, so nothing removes it automatically, and one
left behind would warn about an interaction between two drugs that are no
longer on the record. They are deleted here explicitly.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete, func, select

from app import storage
from app.db.models import (
    Document,
    DocumentPage,
    Finding,
    Patient,
    TimelineEvent,
    TimelineVisit,
)
from app.db.session import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually delete")
    parser.add_argument("--patient", help="clerk_user_id; default is every account")
    args = parser.parse_args()

    db = SessionLocal()

    query = select(Document)
    who = "every account"
    if args.patient:
        patient = db.scalar(select(Patient).where(Patient.clerk_user_id == args.patient))
        if patient is None:
            print(f"No account with clerk_user_id {args.patient!r}.")
            return 1
        query = query.where(Document.patient_id == patient.id)
        who = args.patient

    documents = db.scalars(query).all()
    if not documents:
        print(f"Nothing to delete for {who}.")
        return 0

    ids = [d.id for d in documents]
    count = lambda model, where: db.scalar(  # noqa: E731 - a local shorthand, used four times
        select(func.count()).select_from(model).where(where)
    )

    pages = count(DocumentPage, DocumentPage.document_id.in_(ids))
    visits = count(TimelineVisit, TimelineVisit.document_id.in_(ids))
    visit_ids = db.scalars(
        select(TimelineVisit.id).where(TimelineVisit.document_id.in_(ids))
    ).all()
    events = count(TimelineEvent, TimelineEvent.visit_id.in_(visit_ids)) if visit_ids else 0

    patient_ids = {d.patient_id for d in documents}
    findings = count(Finding, Finding.patient_id.in_(patient_ids))

    print(f"Scope: {who}\n")
    print(f"  documents        {len(documents):>5}")
    print(f"  document pages   {pages:>5}   (cascade)")
    print(f"  timeline visits  {visits:>5}   (cascade)")
    print(f"  timeline events  {events:>5}   (cascade)")
    print(f"  findings         {findings:>5}   (deleted explicitly)")
    print(f"  stored files     {len(documents):>5}   (removed from storage)")
    print("\n  accounts, logins and chat history are kept")

    if not args.apply:
        print("\nDry run. Re-run with --apply to delete. This cannot be undone.")
        return 0

    # Files first, from the paths still on the rows. After the rows are gone
    # there is nothing left that knows where the files were.
    removed = 0
    for document in documents:
        try:
            storage.delete(document.storage_path)
            removed += 1
        except Exception as problem:  # noqa: BLE001 - an unreachable file must not block the row
            print(f"  could not remove {document.storage_path}: {problem}")

    db.execute(delete(Finding).where(Finding.patient_id.in_(patient_ids)))
    db.execute(delete(Document).where(Document.id.in_(ids)))
    db.commit()

    left = db.scalar(select(func.count()).select_from(Document).where(Document.id.in_(ids)))
    orphans = db.scalar(
        select(func.count()).select_from(TimelineVisit).where(TimelineVisit.id.in_(visit_ids))
    ) if visit_ids else 0

    print(f"\ndeleted {len(documents)} documents, {removed} files")
    print(f"rows remaining: documents {left}, visits {orphans}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
