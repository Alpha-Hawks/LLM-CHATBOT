"""
OpenID Connect Provider (authorization code flow with PKCE).

For integrations where Anvaya / ORGMAKER (or the college's identity system) acts as an OpenID
Connect provider. Endpoints are read from the provider's standard discovery document at
OIDC_ISSUER, which is only requested once the college has configured OIDC for this chatbot.

Checks: discovery issuer matches exactly, state (single use), PKCE S256, ID token signature via
the provider's JWKS (public-key algorithms only), iss, aud (+ azp), exp, iat, nonce, and the
claim mapping in identity.base.identity_from_claims.
"""

import base64
import hashlib
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode
import httpx
import jwt

from backend.app.core.config import settings
from backend.app.services.identity.base import (
    AuthenticatedIdentity,
    IdentityError,
    IdentityNotConfigured,
    IdentityProvider,
    identity_from_claims,
)
from backend.app.services.identity.jwks import JwksCache, fetch_json, require_https
from backend.app.services.identity.launch_token import ASYMMETRIC_ALGORITHMS

DISCOVERY_TTL_SECONDS = 3600


def pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OIDCProvider(IdentityProvider):
    name = "oidc"

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._transport = transport
        self._jwks = JwksCache(transport)
        self._discovery: Optional[Tuple[float, str, Dict[str, Any]]] = None

    def is_configured(self) -> bool:
        return bool(settings.OIDC_ISSUER and settings.OIDC_CLIENT_ID and settings.OIDC_REDIRECT_URI)

    async def discovery(self) -> Dict[str, Any]:
        if not self.is_configured():
            raise IdentityNotConfigured("OpenID Connect is not configured.")
        issuer = settings.OIDC_ISSUER
        if self._discovery and self._discovery[1] == issuer and time.time() - self._discovery[0] < DISCOVERY_TTL_SECONDS:
            return self._discovery[2]

        doc = await fetch_json(f"{issuer.rstrip('/')}/.well-known/openid-configuration", "OIDC discovery document", self._transport)
        if doc.get("issuer") != issuer:
            raise IdentityError("OIDC discovery issuer does not match OIDC_ISSUER.")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(doc.get(field), str) or not doc[field]:
                raise IdentityError(f"OIDC discovery document is missing {field}.")
            require_https(doc[field], f"OIDC {field}")
        self._discovery = (time.time(), issuer, doc)
        return doc

    async def authorization_url(self, state: str, nonce: str, code_verifier: str) -> str:
        doc = await self.discovery()
        params = {
            "response_type": "code",
            "client_id": settings.OIDC_CLIENT_ID,
            "redirect_uri": settings.OIDC_REDIRECT_URI,
            "scope": settings.OIDC_SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        endpoint = doc["authorization_endpoint"]
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{urlencode(params)}"

    async def complete_login(self, code: str, code_verifier: str, nonce: str) -> AuthenticatedIdentity:
        doc = await self.discovery()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.OIDC_REDIRECT_URI,
            "client_id": settings.OIDC_CLIENT_ID,
            "code_verifier": code_verifier,
        }
        auth = (settings.OIDC_CLIENT_ID, settings.OIDC_CLIENT_SECRET) if settings.OIDC_CLIENT_SECRET else None
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                resp = await client.post(doc["token_endpoint"], data=form, auth=auth, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise IdentityError(f"Could not reach the OIDC token endpoint ({type(e).__name__}).")
        if resp.status_code != 200:
            raise IdentityError(f"OIDC token exchange failed (HTTP {resp.status_code}).")
        try:
            id_token = resp.json().get("id_token")
        except ValueError:
            raise IdentityError("OIDC token response is not JSON.")
        if not isinstance(id_token, str) or not id_token:
            raise IdentityError("OIDC token response has no id_token.")

        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise IdentityError("ID token is malformed.")
        alg = header.get("alg")
        if alg not in ASYMMETRIC_ALGORITHMS:
            raise IdentityError("ID token must be signed with a public-key algorithm.")

        key = await self._jwks.signing_key(doc["jwks_uri"], header.get("kid"))
        try:
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=[alg],
                audience=settings.OIDC_CLIENT_ID,
                issuer=settings.OIDC_ISSUER,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as e:
            raise IdentityError(f"ID token rejected ({type(e).__name__}).")

        if claims.get("nonce") != nonce:
            raise IdentityError("ID token nonce does not match this sign-in.")
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != settings.OIDC_CLIENT_ID:
            raise IdentityError("ID token azp does not match this client.")

        return identity_from_claims(self.name, settings.OIDC_ISSUER, claims)
