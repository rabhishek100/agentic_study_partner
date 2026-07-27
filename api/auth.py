"""Supabase access-token verification and request-derived ownership.

The verified ``sub`` claim is the only source of ``owner_id``. Request bodies,
query strings, filenames, and client state never determine ownership.
"""

from dataclasses import dataclass
import os
import threading
import time
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from storage.database import parse_owner_id


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


def supabase_url() -> str:
    configured = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not configured:
        raise TokenVerificationError("SUPABASE_URL is not configured")
    return configured


def token_issuer() -> str:
    configured = os.getenv("SUPABASE_JWT_ISSUER", "").strip().rstrip("/")
    return configured or f"{supabase_url()}/auth/v1"


def token_audience() -> str:
    return os.getenv("SUPABASE_JWT_AUDIENCE", "").strip() or DEFAULT_AUDIENCE


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
    secret = os.getenv("SUPABASE_JWT_SECRET", "").strip()
    return secret or None


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
                "token is symmetric but SUPABASE_JWT_SECRET is not configured"
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


_bearer_scheme = HTTPBearer(auto_error=False, description="Supabase access token")

UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="authentication required",
    headers={"WWW-Authenticate": "Bearer"},
)


async def authenticated_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedIdentity:
    """Resolve the verified caller or fail the request with 401."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UNAUTHENTICATED
    try:
        identity = verify_access_token(credentials.credentials)
    except TokenVerificationError as error:
        # The reason stays server-side; tokens and claims are never logged.
        request.state.authentication_error = str(error)
        raise UNAUTHENTICATED from error
    request.state.owner_id = identity.owner_id
    return identity


async def current_owner(
    identity: AuthenticatedIdentity = Depends(authenticated_identity),
) -> UUID:
    """The only supported source of ``owner_id`` in a request handler."""

    return identity.owner_id
