"""
Integration Tests for the OpenID Connect Sign-in Path.
Runs the full authorization-code + PKCE flow against a simulated identity provider
(httpx.MockTransport). Never contacts a real provider.
"""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

try:
    import pytest
except ImportError:
    pytest = None
from backend.app.main import app
from backend.app.services.identity import registry
from backend.app.services.identity.oidc import OIDCProvider
from tests.auth_test_utils import bearer, configured, fragment_params

ISSUER = "https://idp.example.test"
CLIENT_ID = "mlritm-ai-assistant"
REDIRECT_URI = "http://testserver/api/v1/auth/oidc/callback"
IDP_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
IDP_PRIVATE_PEM = IDP_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


class FakeIdentityProvider:
    def __init__(self, discovery_issuer=ISSUER):
        self.discovery_issuer = discovery_issuer
        self.pending = {}  # code -> {challenge, nonce, claims}
        self.nonce_override = None

    def approve(self, authorize_url: str, code: str, **claims) -> str:
        """Simulates the student signing in at the provider; returns the callback query."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID
        assert q["code_challenge_method"] == "S256" and q["redirect_uri"] == REDIRECT_URI
        self.pending[code] = {"challenge": q["code_challenge"], "nonce": q["nonce"], "claims": claims}
        return q["state"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": self.discovery_issuer,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            })
        if path == "/jwks":
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(IDP_KEY.public_key()))
            jwk.update({"kid": "idp-1", "use": "sig", "alg": "RS256"})
            return httpx.Response(200, json={"keys": [jwk]})
        if path == "/token" and request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            grant = self.pending.pop(form.get("code"), None)
            if not grant or form.get("grant_type") != "authorization_code":
                return httpx.Response(400, json={"error": "invalid_grant"})
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != grant["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE mismatch"})
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300,
                      "nonce": self.nonce_override or grant["nonce"], **grant["claims"]}
            id_token = jwt.encode(claims, IDP_PRIVATE_PEM, algorithm="RS256", headers={"kid": "idp-1"})
            return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer", "id_token": id_token})
        return httpx.Response(404)


class use_fake_idp:
    def __init__(self, idp: FakeIdentityProvider):
        self.idp = idp

    def __enter__(self):
        self.saved = registry.providers["oidc"]
        registry.providers["oidc"] = OIDCProvider(transport=httpx.MockTransport(self.idp.handler))
        self.cfg = configured(IDENTITY_PROVIDER="oidc", OIDC_ISSUER=ISSUER, OIDC_CLIENT_ID=CLIENT_ID,
                              OIDC_CLIENT_SECRET="client-secret", OIDC_REDIRECT_URI=REDIRECT_URI)
        self.cfg.__enter__()
        return self.idp

    def __exit__(self, *exc):
        self.cfg.__exit__(*exc)
        registry.providers["oidc"] = self.saved


def _begin(c) -> str:
    res = c.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert res.status_code == 302, res.text
    assert res.headers["location"].startswith(f"{ISSUER}/authorize?")
    return res.headers["location"]


def _callback(c, **params) -> dict:
    res = c.get("/api/v1/auth/oidc/callback", params=params, follow_redirects=False)
    assert res.status_code == 303, res.text
    return fragment_params(res.headers["location"])


def test_oidc_sign_in_creates_session_for_verified_identity():
    with use_fake_idp(FakeIdentityProvider()) as idp, TestClient(app) as c:
        assert c.get("/api/v1/auth/config").json()["sign_in_url"] == "/api/v1/auth/oidc/login"
        state = idp.approve(_begin(c), code="code-1", sub="anvaya-77", roll_no="ROLL-77", name="Student 77")
        params = _callback(c, code="code-1", state=state)
        res = c.post("/api/v1/auth/session/exchange", json={"handoff_code": params["handoff"]})
        assert res.status_code == 200
        me = c.get("/api/v1/auth/me", headers=bearer(res.json()["session_token"])).json()["student"]
        assert me["roll_number"] == "ROLL-77"

        # The same state cannot be used twice
        assert _callback(c, code="code-1", state=state) == {"auth_error": "invalid_state"}


def test_oidc_rejects_unknown_state_and_provider_errors():
    with use_fake_idp(FakeIdentityProvider()), TestClient(app) as c:
        assert _callback(c, code="x", state="never-issued") == {"auth_error": "invalid_state"}
        assert _callback(c, error="access_denied") == {"auth_error": "provider_error"}


def test_oidc_rejects_id_token_with_wrong_nonce():
    with use_fake_idp(FakeIdentityProvider()) as idp, TestClient(app) as c:
        state = idp.approve(_begin(c), code="code-2", sub="anvaya-78", roll_no="ROLL-78")
        idp.nonce_override = "replayed-nonce"
        assert _callback(c, code="code-2", state=state) == {"auth_error": "invalid_login"}


def test_oidc_rejects_discovery_with_mismatched_issuer():
    with use_fake_idp(FakeIdentityProvider(discovery_issuer="https://evil.example.test")), TestClient(app) as c:
        res = c.get("/api/v1/auth/oidc/login", follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == "/app/#auth_error=provider_unavailable"
