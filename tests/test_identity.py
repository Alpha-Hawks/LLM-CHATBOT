"""
Unit Tests for Signed Launch Token Verification.
Covers the checks that decide whether an Anvaya identity assertion is accepted.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

try:
    import pytest
except ImportError:
    pytest = None
from backend.app.services.identity.base import IdentityError, IdentityNotConfigured
from backend.app.services.identity.launch_token import SignedLaunchTokenProvider
from backend.app.services.identity.registry import get_identity_provider
from tests.auth_test_utils import configured, make_launch_token

RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
RSA_PUBLIC_PEM = RSA_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode("ascii")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _verify(token, provider=None):
    return asyncio.run((provider or SignedLaunchTokenProvider()).verify(token))


def _rejected(token, provider=None) -> str:
    try:
        _verify(token, provider)
    except IdentityError as e:
        return str(e)
    raise AssertionError("Expected the launch token to be rejected")


def test_valid_launch_token_maps_claims_server_side():
    with configured():
        identity, claims = _verify(make_launch_token(sub="anvaya-user-42", roll="237Y1A1201"))
        assert identity.subject == "anvaya-user-42"
        assert identity.student_key == "anvaya-user-42"
        assert identity.roll_number == "237Y1A1201"
        assert claims["jti"]


def test_launch_token_wrong_audience_or_issuer_rejected():
    with configured():
        _rejected(make_launch_token(aud="some-other-app"))
        _rejected(make_launch_token(iss="https://attacker.example"))


def test_launch_token_expired_or_long_lived_rejected():
    now = int(time.time())
    with configured():
        _rejected(make_launch_token(iat=now - 1000, exp=now - 500))
        _rejected(make_launch_token(iat=now, exp=now + 3600))
        _rejected(make_launch_token(iat=now + 600, exp=now + 700))


def test_launch_token_forged_signature_rejected():
    with configured():
        _rejected(make_launch_token(key="a-different-secret-that-is-long-enough-000"))


def test_launch_token_alg_none_rejected():
    with configured():
        unsigned = jwt.encode({"iss": "x"}, key="", algorithm="none")
        _rejected(unsigned)
        now = int(time.time())
        unsigned_full = jwt.encode(
            {"iss": "https://anvaya.example.test", "aud": "mlritm-ai-assistant", "sub": "s", "iat": now, "exp": now + 60, "jti": "j"},
            key="", algorithm="none",
        )
        _rejected(unsigned_full)


def test_launch_token_missing_required_claims_rejected():
    with configured():
        _rejected(make_launch_token(jti=None))
        _rejected(make_launch_token(sub=None))
        _rejected(make_launch_token(exp=None))


def test_non_student_accounts_rejected_when_role_restriction_configured():
    with configured(IDENTITY_ROLE_CLAIM="role", IDENTITY_STUDENT_ROLE_VALUES="student"):
        assert "not a student" in _rejected(make_launch_token(role="faculty"))
        _rejected(make_launch_token())  # no role claim at all
        identity, _ = _verify(make_launch_token(role="Student"))
        assert identity.subject == "student-a"


def test_rs256_public_key_verification_and_algorithm_confusion():
    private_pem = RSA_KEY.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with configured(LAUNCH_TOKEN_ALGORITHMS="RS256", LAUNCH_TOKEN_HMAC_SECRET="", LAUNCH_TOKEN_PUBLIC_KEY=RSA_PUBLIC_PEM):
        identity, _ = _verify(make_launch_token(key=private_pem, algorithm="RS256"))
        assert identity.subject == "student-a"
        # Algorithm confusion: an attacker HMAC-signs with the PUBLIC key as the secret
        now = int(time.time())
        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64(json.dumps({"iss": "https://anvaya.example.test", "aud": "mlritm-ai-assistant", "sub": "victim",
                                   "iat": now, "exp": now + 60, "jti": "x"}).encode())
        signature = _b64(hmac.new(RSA_PUBLIC_PEM.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        _rejected(f"{header}.{payload}.{signature}")


def test_launch_token_via_jwks_url_with_key_rotation():
    private_pem = RSA_KEY.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    old_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    old_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(old_key.public_key()))
    old_jwk.update({"kid": "anvaya-key-1", "use": "sig", "alg": "RS256"})
    new_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(RSA_KEY.public_key()))
    new_jwk.update({"kid": "anvaya-key-2", "use": "sig", "alg": "RS256"})
    fetches = []

    def handler(request: httpx.Request) -> httpx.Response:
        # First fetch serves only the old key; afterwards the rotated key set
        fetches.append(str(request.url))
        return httpx.Response(200, json={"keys": [old_jwk] if len(fetches) == 1 else [old_jwk, new_jwk]})

    with configured(LAUNCH_TOKEN_ALGORITHMS="RS256", LAUNCH_TOKEN_HMAC_SECRET="",
                    LAUNCH_TOKEN_JWKS_URL="https://anvaya.example.test/jwks"):
        provider = SignedLaunchTokenProvider(transport=httpx.MockTransport(handler))
        identity, _ = _verify(make_launch_token(key=private_pem, algorithm="RS256", headers={"kid": "anvaya-key-2"}), provider)
        assert identity.subject == "student-a"
        assert len(fetches) == 2  # unknown kid triggered exactly one refresh
        _rejected(make_launch_token(key=private_pem, algorithm="RS256", headers={"kid": "unknown-kid"}), provider)
        # A token signed by a key that is not in Anvaya's JWKS, reusing a published kid
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        _rejected(make_launch_token(key=other, algorithm="RS256", headers={"kid": "anvaya-key-2"}), provider)


def test_unconfigured_or_unsafe_launch_settings_disable_sign_in():
    with configured(LAUNCH_TOKEN_ISSUER=""):
        assert get_identity_provider() is None
        try:
            _verify(make_launch_token())
            raise AssertionError("Expected IdentityNotConfigured")
        except IdentityNotConfigured:
            pass
    with configured(LAUNCH_TOKEN_ALGORITHMS="HS256,RS256"):
        assert get_identity_provider() is None
    with configured(LAUNCH_TOKEN_ALGORITHMS="none"):
        assert get_identity_provider() is None
    with configured(LAUNCH_TOKEN_HMAC_SECRET="too-short"):
        assert get_identity_provider() is None
    with configured(IDENTITY_PROVIDER="none"):
        assert get_identity_provider() is None
