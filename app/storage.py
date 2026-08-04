"""File storage for uploaded documents.

The database holds a key; this module decides what that key means. Two backends
behind one set of functions:

  * Supabase Storage, when SUPABASE_URL and SUPABASE_SERVICE_KEY are set;
  * the local filesystem otherwise.

The environment picks, and nothing above this line knows which it got. That is
the whole reason the module existed before it did anything interesting: three
call sites use it — save on upload, read during ingestion, delete on removal —
and swapping the backend touched none of them.

Keys are content-addressed (patient / sha256.ext), so the same bytes saved
twice are idempotent and never duplicated, and the same key names the same file
under either backend. That is what lets existing local files be pushed up
without rewriting a single `storage_path` in the database.

Why not the filesystem on a host: a container's disk does not survive a
redeploy. The facts extracted from a document live in Postgres and come back;
the document itself does not. A timeline that cites page 1 of a prescription no
one can open again is worse than one that never cited it.

The bucket is private. Documents are served through `signed_url`, which mints a
link good for a few minutes — after this app has checked the document belongs
to whoever is asking. Supabase serves the bytes; the decision stays here.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

import httpx

from app.config import (
    SIGNED_URL_TTL,
    SUPABASE_BUCKET,
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
    UPLOAD_DIR,
)

logger = logging.getLogger(__name__)

_EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

# Generous: these are single documents, not archives, and the slow part is a
# phone photo of a prescription over a home connection.
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def using_supabase() -> bool:
    """Whether uploads go to object storage rather than the local disk."""
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY or "",
    }


def _object_url(key: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{key}"


def _is_missing(response: httpx.Response) -> bool:
    """Whether a failed response means 'no such object' rather than 'broken'.

    Supabase answers a missing object with HTTP 400 and a body that says 404:

        400 {"statusCode":"404","error":"not_found","code":"NoSuchKey"}

    Reading the status alone gets this exactly wrong — a document whose original
    is gone looks like storage being down, and the difference matters to every
    caller. One is a file lost to an old redeploy, which the screen should say
    plainly; the other is an outage, which it should not blame on the file.
    """
    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return str(body.get("statusCode")) == "404" or body.get("code") == "NoSuchKey"


class StorageError(RuntimeError):
    """A backend refused. Raised rather than returned so an upload fails loudly.

    Silently falling back to the local disk here would be worse than failing:
    the document would look saved, work until the next redeploy, and disappear
    with no record of why.
    """


def compute_sha256(data: bytes) -> str:
    """Hash of the raw bytes. Used to detect re-uploads of the same file."""
    return hashlib.sha256(data).hexdigest()


def key_for(patient_id: UUID, sha256: str, mime_type: str) -> str:
    """Where these bytes belong, under either backend."""
    return f"{patient_id}/{sha256}{_EXTENSIONS.get(mime_type, '')}"


def ensure_bucket() -> None:
    """Create the bucket if it is not there yet. Safe to call repeatedly.

    Private, explicitly. A public bucket is one checkbox and it would make the
    viewer simpler by making every prescription readable by anyone holding the
    URL, for ever. Links leak; expiries are the point.
    """
    if not using_supabase():
        return
    with httpx.Client(timeout=_TIMEOUT) as client:
        existing = client.get(f"{SUPABASE_URL}/storage/v1/bucket", headers=_headers())
        if existing.is_success and any(
            b.get("name") == SUPABASE_BUCKET for b in existing.json()
        ):
            return
        made = client.post(
            f"{SUPABASE_URL}/storage/v1/bucket",
            headers=_headers(),
            json={"id": SUPABASE_BUCKET, "name": SUPABASE_BUCKET, "public": False},
        )
        if not made.is_success and made.status_code != 409:
            raise StorageError(f"could not create bucket: {made.status_code} {made.text}")
        logger.info("created private storage bucket %r", SUPABASE_BUCKET)


def save(patient_id: UUID, sha256: str, mime_type: str, data: bytes) -> str:
    """Write the file and return the key to record on the document."""
    key = key_for(patient_id, sha256, mime_type)

    if not using_supabase():
        path = UPLOAD_DIR / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():  # content-addressed: identical bytes need no rewrite
            path.write_bytes(data)
        return key

    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.post(
            _object_url(key),
            headers={
                **_headers(),
                "Content-Type": mime_type,
                "x-upsert": "true",
                # Supabase caches objects at the edge for an hour by default,
                # and a deleted file goes on being served from that cache long
                # after it is gone. Matched to the signed-link lifetime instead:
                # nothing can outlive the link that reaches it. Cheap, because
                # keys are content-addressed — a cached response is never the
                # wrong bytes, only bytes that should have stopped existing.
                "cache-control": f"max-age={SIGNED_URL_TTL}",
            },
            content=data,
        )
    # x-upsert makes a repeat upload a no-op rather than a 409, which is what
    # content-addressing already promised: same bytes, same key, same file.
    if not response.is_success:
        raise StorageError(f"upload failed: {response.status_code} {response.text}")
    return key


def read(key: str) -> bytes:
    """Read a stored file back. Raises FileNotFoundError if it is gone."""
    if not using_supabase():
        return (UPLOAD_DIR / key).read_bytes()

    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.get(_object_url(key), headers=_headers())
    if _is_missing(response):
        # The same exception the local backend raises, so callers written
        # against the filesystem keep working unchanged.
        raise FileNotFoundError(key)
    if not response.is_success:
        raise StorageError(f"download failed: {response.status_code} {response.text}")
    return response.content


def delete(key: str) -> None:
    """Remove a stored file. Silent if it is already gone."""
    if not using_supabase():
        (UPLOAD_DIR / key).unlink(missing_ok=True)
        return

    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.delete(_object_url(key), headers=_headers())
    if not response.is_success and response.status_code != 404:
        # A file left behind costs storage; a failed request that takes the
        # whole delete down with it costs the user their document row.
        logger.warning("could not delete %s: %s %s", key, response.status_code, response.text)


def signed_url(key: str, ttl: int = SIGNED_URL_TTL) -> str | None:
    """A time-limited link the browser can load directly.

    None on the local backend, which has no such thing — the caller serves the
    bytes itself there. Worth the branch: an <img> tag sends no Authorization
    header, so without a signed link every document has to be fetched as a blob
    and handed to the tag as an object URL.
    """
    if not using_supabase():
        return None

    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.post(
            f"{SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_BUCKET}/{key}",
            headers=_headers(),
            json={"expiresIn": ttl},
        )
    if _is_missing(response):
        raise FileNotFoundError(key)
    if not response.is_success:
        raise StorageError(f"could not sign {key}: {response.status_code} {response.text}")

    # Returned relative, e.g. '/object/sign/documents/<key>?token=…'
    return f"{SUPABASE_URL}/storage/v1{response.json()['signedURL']}"
