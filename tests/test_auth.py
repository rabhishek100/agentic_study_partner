"""Access-token verification rules.

These tests mint tokens with a locally generated key rather than calling
Supabase, so every rejection reason can be exercised deterministically.
"""

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from api.auth import (
    AuthenticatedIdentity,
    TokenVerificationError,
    reset_signing_keys,
    verify_access_token,
)


ISSUER = "http://127.0.0.1:54321/auth/v1"
KEY_ID = "test-key-1"
OTHER_KEY_ID = "test-key-2"


def _jwks_entry(public_key, key_id: str) -> dict:
    entry = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public_key))
    entry.update({"kid": key_id, "alg": "ES256", "use": "sig"})
    return entry


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class AccessTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = ec.generate_private_key(ec.SECP256R1())
        cls.other_private_key = ec.generate_private_key(ec.SECP256R1())
        cls.jwks = {
            "keys": [
                _jwks_entry(cls.private_key.public_key(), KEY_ID),
                _jwks_entry(cls.other_private_key.public_key(), OTHER_KEY_ID),
            ]
        }

    def setUp(self):
        reset_signing_keys()
        self.environment = patch.dict(
            os.environ,
            {"SUPABASE_URL": "http://127.0.0.1:54321", "SUPABASE_JWT_SECRET": ""},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(reset_signing_keys)

        self.fetch = patch("api.auth.httpx.get", return_value=_Response(self.jwks))
        self.fetch.start()
        self.addCleanup(self.fetch.stop)

        self.subject = uuid4()

    def token(self, *, key=None, key_id=KEY_ID, algorithm="ES256", **overrides) -> str:
        issued = datetime.now(timezone.utc)
        claims = {
            "iss": ISSUER,
            "sub": str(self.subject),
            "aud": "authenticated",
            "role": "authenticated",
            "email": "reader@example.com",
            "iat": issued,
            "exp": issued + timedelta(hours=1),
        }
        claims.update(overrides)
        headers = {"kid": key_id} if key_id else {}
        return jwt.encode(
            claims,
            key if key is not None else self.private_key,
            algorithm=algorithm,
            headers=headers,
        )

    def assert_rejected(self, token: str) -> None:
        with self.assertRaises(TokenVerificationError):
            verify_access_token(token)

    def test_accepts_a_valid_token(self):
        identity = verify_access_token(self.token())

        self.assertEqual(
            identity,
            AuthenticatedIdentity(
                owner_id=self.subject,
                email="reader@example.com",
                role="authenticated",
            ),
        )

    def test_rejects_a_token_signed_by_another_key(self):
        # Correct kid, wrong private key: the signature must not verify.
        self.assert_rejected(self.token(key=self.other_private_key, key_id=KEY_ID))

    def test_rejects_an_unpublished_key_id(self):
        self.assert_rejected(self.token(key_id="unknown-key"))

    def test_rejects_an_expired_token(self):
        expired = datetime.now(timezone.utc) - timedelta(hours=2)
        self.assert_rejected(self.token(exp=expired, iat=expired))

    def test_rejects_a_token_that_is_not_yet_valid(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assert_rejected(self.token(nbf=future))

    def test_rejects_a_foreign_issuer(self):
        self.assert_rejected(self.token(iss="https://attacker.example.com/auth/v1"))

    def test_rejects_a_foreign_audience(self):
        self.assert_rejected(self.token(aud="some-other-service"))

    def test_rejects_a_non_authenticated_role(self):
        self.assert_rejected(self.token(role="anon"))
        self.assert_rejected(self.token(role="service_role"))

    def test_rejects_a_subject_that_is_not_a_uuid(self):
        self.assert_rejected(self.token(sub="not-a-uuid"))

    def test_rejects_missing_required_claims(self):
        for claim in ("sub", "aud", "iss", "exp"):
            with self.subTest(claim=claim):
                payload = {
                    "iss": ISSUER,
                    "sub": str(self.subject),
                    "aud": "authenticated",
                    "role": "authenticated",
                    "iat": datetime.now(timezone.utc),
                    "exp": datetime.now(timezone.utc) + timedelta(hours=1),
                }
                payload.pop(claim)
                self.assert_rejected(
                    jwt.encode(
                        payload,
                        self.private_key,
                        algorithm="ES256",
                        headers={"kid": KEY_ID},
                    )
                )

    def test_rejects_an_unsigned_token(self):
        payload = {
            "iss": ISSUER,
            "sub": str(self.subject),
            "aud": "authenticated",
            "role": "authenticated",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "iat": datetime.now(timezone.utc),
        }
        self.assert_rejected(jwt.encode(payload, key=None, algorithm="none"))

    def test_rejects_empty_and_malformed_tokens(self):
        for value in ("", "   ", "garbage", "a.b.c"):
            with self.subTest(token=value):
                self.assert_rejected(value)

    def test_rejects_a_symmetric_token_when_no_secret_is_configured(self):
        # An asymmetric project must not be tricked into HMAC verification.
        self.assert_rejected(
            self.token(key="super-secret-value", key_id=None, algorithm="HS256")
        )

    def test_accepts_a_symmetric_token_when_the_secret_matches(self):
        secret = "local-development-secret-of-at-least-32-chars"
        with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": secret}):
            identity = verify_access_token(
                self.token(key=secret, key_id=None, algorithm="HS256")
            )
            self.assertEqual(identity.owner_id, self.subject)

            self.assert_rejected(
                self.token(key="a-different-secret", key_id=None, algorithm="HS256")
            )

    def test_reports_unavailable_signing_keys_without_accepting_the_token(self):
        with patch("api.auth.httpx.get", side_effect=OSError("network down")):
            reset_signing_keys()
            self.assert_rejected(self.token())


if __name__ == "__main__":
    unittest.main()
