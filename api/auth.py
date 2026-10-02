"""Access-token verification and request-derived ownership.

The verified ``sub`` claim is the only source of ``owner_id``. Request bodies,
query strings, filenames, and client state never determine ownership.

Configuration is named ``AUTH_*`` rather than ``SUPABASE_*``. Those used to be
the same thing; since the database moved to Railway and source PDFs to R2 they
are not, and a single ``SUPABASE_URL`` shared between the token issuer and an
object store is a variable that means two things and can only be set to one.
The old names still work for one release, with a warning that names the
variable and never its value.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
import logging
import os
import threading
import time
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from storage.application_users import (
    IdentityConflict,
    VerifiedIdentity,
    ensure_application_user,
)
from storage.database import connection, parse_owner_id


logger = logging.getLogger(__name__)


ASYMMETRIC_ALGORITHMS = ("ES256", "RS256", "EdDSA")
SYMMETRIC_ALGORITHMS = ("HS256",)
AUTHENTICATED_ROLE = "authenticated"
DEFAULT_AUDIENCE = "authenticated"
JWKS_CACHE_SECONDS = 600
JWKS_MIN_REFRESH_SECONDS = 30
CLOCK_SKEW_SECONDS = 10


class TokenVerificationError(Exception):
    """The presented access token cannot be trusted.

    The message is for server logs. Clients only ever see a generic 401 so a
    token's contents are never reflected back to the caller.
    """


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """The verified subject of one request."""

    owner_id: UUID
    email: str | None
    role: str


_DEPRECATED_NAMES = {
    "AUTH_SUPABASE_URL": "SUPABASE_URL",
    "AUTH_JWT_ISSUER": "SUPABASE_JWT_ISSUER",
    "AUTH_JWT_AUDIENCE": "SUPABASE_JWT_AUDIENCE",
    "AUTH_JWT_SECRET": "SUPABASE_JWT_SECRET",
}
_WARNED: set[str] = set()


def _auth_setting(name: str) -> str:
    """Read an ``AUTH_*`` variable, falling back to its old ``SUPABASE_*`` name.

    The fallback is a compatibility release, not a permanent alias: it warns
    once per process per variable so a environment still on the old names is
    visible in logs without being noisy. Remove it once every environment has
    moved. The warning names the variable and never its value.
    """

    value = os.getenv(name, "").strip()
    if value:
        return value

    legacy = _DEPRECATED_NAMES.get(name)
    if not legacy:
        return ""
    value = os.getenv(legacy, "").strip()
    if value and legacy not in _WARNED:
        _WARNED.add(legacy)
        logger.warning(
            "%s is deprecated and will stop being read; set %s instead",
            legacy,
            name,
        )
    return value


def reset_deprecation_warnings() -> None:
    """Allow the one-shot deprecation warnings to fire again. For tests."""

    _WARNED.clear()


def auth_url() -> str:
    """The identity provider's base URL. No longer the object store's."""

    configured = _auth_setting("AUTH_SUPABASE_URL").rstrip("/")
    if not configured:
        raise TokenVerificationError("AUTH_SUPABASE_URL is not configured")
    return configured


def token_issuer() -> str:
    configured = _auth_setting("AUTH_JWT_ISSUER").rstrip("/")
    return configured or f"{auth_url()}/auth/v1"


def token_audience() -> str:
    return _auth_setting("AUTH_JWT_AUDIENCE") or DEFAULT_AUDIENCE


class _JwksCache:
    """Cache Supabase's public signing keys and refresh them on an unknown kid.

    Refresh is rate limited so a stream of tokens signed with a bogus kid
    cannot turn into a stream of outbound requests.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    def reset(self) -> None:
        with self._lock:
            self._keys = {}
            self._fetched_at = 0.0

    def key_for(self, kid: str | None) -> Any:
        with self._lock:
            fresh = time.monotonic() - self._fetched_at < JWKS_CACHE_SECONDS
            known = kid is not None and kid in self._keys
            if not (fresh and known):
                self._refresh_locked(force=not known)
            if kid is None:
                if len(self._keys) != 1:
                    raise TokenVerificationError(
                        "token has no key id and the issuer publishes several"
                    )
                return next(iter(self._keys.values()))
            if kid not in self._keys:
                raise TokenVerificationError("token key id is not published by issuer")
            return self._keys[kid]

    def _refresh_locked(self, *, force: bool) -> None:
        age = time.monotonic() - self._fetched_at
        if self._keys and not force and age < JWKS_CACHE_SECONDS:
            return
        if self._keys and force and age < JWKS_MIN_REFRESH_SECONDS:
            return
        url = f"{token_issuer()}/.well-known/jwks.json"
        try:
            response = httpx.get(url, timeout=5.0)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, OSError, ValueError) as error:
            # Any failure to fetch keys must surface as a rejected token, not
            # as an unhandled error that becomes a 500.
            raise TokenVerificationError(
                "issuer signing keys are unavailable"
            ) from error

        keys: dict[str, Any] = {}
        for entry in document.get("keys", []):
            if entry.get("alg") and entry["alg"] not in ASYMMETRIC_ALGORITHMS:
                continue
            try:
                keys[entry.get("kid")] = jwt.PyJWK(entry).key
            except (jwt.exceptions.PyJWKError, jwt.exceptions.InvalidKeyError):
                continue
        self._keys = keys
        self._fetched_at = time.monotonic()


_JWKS = _JwksCache()


def reset_signing_keys() -> None:
    """Drop cached issuer keys. Used by tests and after a key rotation."""

    _JWKS.reset()


def _symmetric_secret() -> str | None:
    return _auth_setting("AUTH_JWT_SECRET") or None


def _decode(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise TokenVerificationError("token header is malformed") from error

    algorithm = header.get("alg")
    options = {"require": ["exp", "iat", "sub", "aud", "iss"]}
    common = {
        "audience": token_audience(),
        "issuer": token_issuer(),
        "leeway": CLOCK_SKEW_SECONDS,
        "options": options,
    }

    if algorithm in ASYMMETRIC_ALGORITHMS:
        key = _JWKS.key_for(header.get("kid"))
        algorithms = [algorithm]
    elif algorithm in SYMMETRIC_ALGORITHMS:
        # Projects still on a shared JWT secret, including the local CLI stack.
        key = _symmetric_secret()
        if key is None:
            raise TokenVerificationError(
                "token is symmetric but AUTH_JWT_SECRET is not configured"
            )
        algorithms = list(SYMMETRIC_ALGORITHMS)
    else:
        raise TokenVerificationError(f"unsupported token algorithm: {algorithm!r}")

    try:
        return jwt.decode(token, key, algorithms=algorithms, **common)
    except jwt.PyJWTError as error:
        raise TokenVerificationError(f"token rejected: {type(error).__name__}") from error


def verify_access_token(token: str) -> AuthenticatedIdentity:
    """Validate signature, issuer, audience, lifetime, role, and subject."""

    if not token or not token.strip():
        raise TokenVerificationError("empty bearer token")

    claims = _decode(token.strip())
    role = claims.get("role")
    if role != AUTHENTICATED_ROLE:
        raise TokenVerificationError(f"token role is not authenticated: {role!r}")
    try:
        owner_id = parse_owner_id(claims["sub"])
    except (KeyError, ValueError) as error:
        raise TokenVerificationError("token subject is not a UUID") from error

    email = claims.get("email")
    return AuthenticatedIdentity(
        owner_id=owner_id,
        email=email if isinstance(email, str) and email else None,
        role=role,
    )


_bearer_scheme = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="Supabase session access JWT. Paste the raw token without the Bearer prefix; do not use the anon key or refresh token.",
)

UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="authentication required",
    headers={"WWW-Authenticate": "Bearer"},
)


async def authenticated_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AsyncIterator[AuthenticatedIdentity]:
    """Resolve the verified caller or fail the request with 401."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UNAUTHENTICATED
    try:
        identity = verify_access_token(credentials.credentials)
    except TokenVerificationError as error:
        # The reason stays server-side; tokens and claims are never logged.
        request.state.authentication_error = str(error)
        raise UNAUTHENTICATED from error
    from observability import authenticated_user
    request.state.owner_id = identity.owner_id
    with authenticated_user(identity.owner_id):
        yield identity


IDENTITY_CONFLICT = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="this account cannot be reconciled with an existing one",
)


def _register(identity: AuthenticatedIdentity) -> None:
    """Make the database's identity registry aware of a verified caller.

    On Supabase this row was GoTrue's and always existed. On Railway the Auth
    project is a separate system, so the first authenticated request from a
    new account would otherwise fail on the `owner_id` foreign key. Done here
    because `current_owner` is the single gate every tenant-owned write passes
    through — 134 call sites, and nothing reaches a repository without it.

    A registry that is unreachable must not read as an authentication failure:
    the token was fine, the database was not, so that surfaces as a 503 from
    the handler rather than a 401 that tells the reader to sign in again.
    """

    with connection() as database:
        ensure_application_user(
            database,
            VerifiedIdentity(owner_id=identity.owner_id, email=identity.email),
        )


async def current_owner(
    identity: AuthenticatedIdentity = Depends(authenticated_identity),
) -> UUID:
    """The only supported source of ``owner_id`` in a request handler."""

    try:
        await run_in_threadpool(_register, identity)
    except IdentityConflict as error:
        raise IDENTITY_CONFLICT from error
    return identity.owner_id
