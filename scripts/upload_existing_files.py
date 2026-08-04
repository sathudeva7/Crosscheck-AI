"""Push documents already on the local disk into Supabase Storage.

Run once, after setting SUPABASE_URL and SUPABASE_SERVICE_KEY:

    .venv/bin/python -m scripts.upload_existing_files          # show what would move
    .venv/bin/python -m scripts.upload_existing_files --apply  # move it

Nothing in the database changes. Keys are content-addressed — patient/sha256.ext
— so a file has the same key on the disk as it does in the bucket, and every
`storage_path` already recorded keeps pointing at the right object once the
bytes are up there.

Safe to re-run. Uploads are upserts, and a document whose file is already in the
bucket is skipped without reading it.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app import storage
from app.config import SUPABASE_BUCKET, UPLOAD_DIR
from app.db.models import Document
from app.db.session import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually upload")
    args = parser.parse_args()

    if not storage.using_supabase():
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY are not set — nothing to upload to.")
        return 1

    storage.ensure_bucket()

    db = SessionLocal()
    documents = db.scalars(select(Document).order_by(Document.created_at)).all()

    missing: list[Document] = []
    ready: list[tuple[Document, bytes]] = []

    for document in documents:
        local = UPLOAD_DIR / document.storage_path
        if not local.exists():
            missing.append(document)
            continue
        ready.append((document, local.read_bytes()))

    print(f"documents in database : {len(documents)}")
    print(f"files found on disk   : {len(ready)}")
    print(f"files already gone    : {len(missing)}")
    print(f"bucket                : {SUPABASE_BUCKET}")

    if missing:
        # Worth naming. These are rows whose original was lost to an earlier
        # redeploy; the extracted facts are still good but the page behind them
        # cannot be shown, and the viewer will have to say so rather than break.
        print("\nno local file (their originals are gone for good):")
        for document in missing[:10]:
            print(f"  {document.filename or document.id}")
        if len(missing) > 10:
            print(f"  … and {len(missing) - 10} more")

    if not args.apply:
        print("\nDry run. Re-run with --apply to upload.")
        return 0

    print()
    uploaded = failed = 0
    for document, data in ready:
        try:
            storage.save(document.patient_id, document.sha256, document.mime_type, data)
            uploaded += 1
            print(f"  ok    {document.storage_path}")
        except Exception as problem:  # noqa: BLE001 - report and keep going
            failed += 1
            print(f"  FAIL  {document.storage_path}: {problem}")

    print(f"\nuploaded {uploaded}, failed {failed}")
    db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
