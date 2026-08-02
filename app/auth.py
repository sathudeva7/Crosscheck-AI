"""Auth boundary.

Clerk signs a short-lived session token in the browser; this module verifies it
and hands the rest of the app a user id. Routes depend on these two functions
rather than on Clerk directly, so nothing else in the codebase knows or cares
which identity provider is in use.

Verification is asymmetric: Clerk holds the private key, we fetch the matching
public key from its JWKS endpoint. That means no shared secret and no call back
to Clerk on the request path — the key is cached after the first fetch.

With `CLERK_ISSUER` unset the demo stub below takes over and every request
resolves to one fixed account. That keeps local work possible without a Clerk
project, and it is the reason the startup warning is loud: a deployment that
forgets the variable serves one patient's records to everybody.
"""

import logging
import os

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import CLERK_AUTHORIZED_PARTIES, CLERK_CLOCK_SKEW_SECONDS, CLERK_ISSUER
from app.db.models import Patient
from app.db.session import get_db

log = logging.getLogger(__name__)

DEMO_CLERK_USER_ID = os.getenv("DEMO_CLERK_USER_ID", "demo-user")

# auto_error=False so a missing header reaches our own code, which can then
# decide between "demo mode, carry on" and a 401.
_bearer = HTTPBearer(auto_error=False, description="Clerk session token")

# One client for the process. It caches signing keys, so the JWKS endpoint is
# hit once rather than once per request, and re-fetches only when Clerk rotates
# to a key id it has not seen.
_jwks: PyJWKClient | None = (
    PyJWKClient(f"{CLERK_ISSUER}/.well-known/jwks.json", cache_keys=True)
    if CLERK_ISSUER
    else None
)

if _jwks is None:
    log.warning(
        "CLERK_ISSUER is not set — every request will resolve to the demo "
        "account %r. Never run this outside local development.",
        DEMO_CLERK_USER_ID,
    )


def _unauthorised(reason: str) -> HTTPException:
    """401 with a WWW-Authenticate header, as the bearer scheme requires."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=reason,
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_clerk_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """The authenticated Clerk user id, taken from the token's `sub` claim.

    Sync rather than async on purpose: the first call may block fetching the
    JWKS, and FastAPI runs sync dependencies in a threadpool where blocking is
    harmless. An async version would stall the event loop instead.
    """
    if _jwks is None:
        return DEMO_CLERK_USER_ID

    if credentials is None:
        raise _unauthorised("Not authenticated")

    token = credentials.credentials

    try:
        signing_key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=CLERK_ISSUER,
            leeway=CLERK_CLOCK_SKEW_SECONDS,
            # Clerk session tokens carry no `aud`, so asking for one would fail
            # every valid token. `azp` below is the equivalent check.
            options={"verify_aud": False, "require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        # Its own message: the frontend SDK refreshes silently, so this usually
        # means a clock problem rather than an idle user.
        raise _unauthorised("Session expired") from None
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as problem:
        log.info("Rejected token: %s", problem)
        raise _unauthorised("Invalid session token") from None

    if CLERK_AUTHORIZED_PARTIES:
        party = claims.get("azp")
        if party not in CLERK_AUTHORIZED_PARTIES:
            log.warning("Rejected token from unexpected origin %r", party)
            raise _unauthorised("Invalid session token")

    user_id = claims.get("sub")
    if not user_id:
        raise _unauthorised("Invalid session token")

    return user_id


def current_patient(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(current_clerk_user_id),
) -> Patient:
    """Resolve the signed-in account to its patient row.

    404 rather than 401 when missing: the caller is authenticated but has not
    completed registration yet, which is the frontend's cue to show the profile
    form.
    """
    patient = db.scalar(select(Patient).where(Patient.clerk_user_id == clerk_user_id))
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No patient profile registered"
        )
    return patient
