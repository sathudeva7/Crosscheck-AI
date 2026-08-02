"""File storage for uploaded documents.

Files live on disk; the database only holds their path. Backed by the local
filesystem for now — swapping in Supabase Storage later means reimplementing
these four functions and nothing else.

Paths are content-addressed (patient / sha256.ext), so saving the same bytes
twice is idempotent and never duplicates a file.
"""

import hashlib
from pathlib import Path
from uuid import UUID

from app.config import UPLOAD_DIR

_EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


def compute_sha256(data: bytes) -> str:
    """Hash of the raw bytes. Used to detect re-uploads of the same file."""
    return hashlib.sha256(data).hexdigest()


def save(patient_id: UUID, sha256: str, mime_type: str, data: bytes) -> str:
    """Write the file and return the storage path to record on the document."""
    directory = UPLOAD_DIR / str(patient_id)
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{sha256}{_EXTENSIONS.get(mime_type, '')}"
    if not path.exists():  # content-addressed, so identical bytes need no rewrite
        path.write_bytes(data)

    return str(path.relative_to(UPLOAD_DIR))


def read(storage_path: str) -> bytes:
    """Read a stored file back. Raises FileNotFoundError if it is gone."""
    return (UPLOAD_DIR / storage_path).read_bytes()


def delete(storage_path: str) -> None:
    """Remove a stored file. Silent if it is already gone."""
    (UPLOAD_DIR / storage_path).unlink(missing_ok=True)


def absolute_path(storage_path: str) -> Path:
    """Full path on disk, for libraries that want a filename rather than bytes."""
    return UPLOAD_DIR / storage_path
