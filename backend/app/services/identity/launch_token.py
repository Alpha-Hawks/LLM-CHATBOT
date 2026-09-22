"""
Signed Launch Token Provider ("secure launch URL").

For integrations where Anvaya opens the assistant with a short-lived JWT that it signed, e.g.
    POST https://<chatbot>/api/v1/auth/launch   (form field: launch_token)
The issuer, audience, algorithm and verification key must come from the college / ORGMAKER.
Nothing here assumes Anvaya already supports this; it stays disabled until configured.

Checks: signature (algorithm allow-list, never "none"), iss, aud, exp, iat, maximum token age
and lifetime, required jti (single use, enforced by the sessions service), and the claim mapping
in identity.base.identity_from_claims.
"""

import time
from typing import Any, Dict, List, Optional, Tuple
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
from backend.app.services.identity.jwks import JwksCache

ASYMMETRIC_ALGORITHMS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
HMAC_ALGORITHMS = {"HS256", "HS384", "HS512"}
CLOCK_SKEW_SECONDS = 30
MAX_TOKEN_LENGTH = 8192


class SignedLaunchTokenProvider(IdentityProvider):
    name = "signed_launch"

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._jwks = JwksCache(transport)

    def algorithms(self) -> List[str]:
        algs = [a.strip() for a in settings.LAUNCH_TOKEN_ALGORITHMS.split(",") if a.strip()]
        if not algs or any(a not in ASYMMETRIC_ALGORITHMS | HMAC_ALGORITHMS for a in algs):
            raise IdentityNotConfigured("LAUNCH_TOKEN_ALGORITHMS must list supported JWT algorithms (never 'none').")
        if any(a in HMAC_ALGORITHMS for a in algs) and any(a in ASYMMETRIC_ALGORITHMS for a in algs):
            raise IdentityNotConfigured("LAUNCH_TOKEN_ALGORITHMS must not mix HMAC and public-key algorithms.")
        return algs

    def uses_hmac(self) -> bool:
        return self.algorithms()[0] in HMAC_ALGORITHMS

    def is_configured(self) -> bool:
        try:
            hmac = self.uses_hmac()
        except IdentityNotConfigured:
            return False
        if not (settings.LAUNCH_TOKEN_ISSUER and settings.LAUNCH_TOKEN_AUDIENCE):
            return False
        if hmac:
            return len(settings.LAUNCH_TOKEN_HMAC_SECRET.encode("utf-8")) >= 32
        return bool(settings.LAUNCH_TOKEN_PUBLIC_KEY or settings.LAUNCH_TOKEN_PUBLIC_KEY_FILE or settings.LAUNCH_TOKEN_JWKS_URL)

    async def _verification_key(self, token: str) -> Any:
        if self.uses_hmac():
            return settings.LAUNCH_TOKEN_HMAC_SECRET
        if settings.LAUNCH_TOKEN_JWKS_URL:
            try:
                kid = jwt.get_unverified_header(token).get("kid")
            except jwt.PyJWTError:
                raise IdentityError("Launch token is malformed.")
            return await self._jwks.signing_key(settings.LAUNCH_TOKEN_JWKS_URL, kid)
        if settings.LAUNCH_TOKEN_PUBLIC_KEY:
            return settings.LAUNCH_TOKEN_PUBLIC_KEY.replace("\\n", "\n")
        with open(settings.LAUNCH_TOKEN_PUBLIC_KEY_FILE, "r", encoding="utf-8") as f:
            return f.read()

    async def verify(self, token: str) -> Tuple[AuthenticatedIdentity, Dict[str, Any]]:
        """Returns the verified identity and claims. The caller must still consume the jti once."""
        if not self.is_configured():
            raise IdentityNotConfigured("Signed launch tokens are not configured.")
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise IdentityError("Launch token is missing or too long.")

        key = await self._verification_key(token)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=self.algorithms(),
                audience=settings.LAUNCH_TOKEN_AUDIENCE,
                issuer=settings.LAUNCH_TOKEN_ISSUER,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
            )
        except jwt.PyJWTError as e:
            raise IdentityError(f"Launch token rejected ({type(e).__name__}).")

        now = time.time()
        max_age = min(120, settings.LAUNCH_TOKEN_MAX_AGE_SECONDS)
        iat, exp = float(claims["iat"]), float(claims["exp"])
        if iat > now + CLOCK_SKEW_SECONDS:
            raise IdentityError("Launch token was issued in the future.")
        if now - iat > max_age + CLOCK_SKEW_SECONDS or exp - iat > max_age + CLOCK_SKEW_SECONDS:
            raise IdentityError("Launch token is older or longer-lived than allowed.")
        if not str(claims["jti"]).strip():
            raise IdentityError("Launch token has an empty jti.")
        if not str(claims.get("sub", "")).strip():
            raise IdentityError("Launch token has an empty sub claim.")

        return identity_from_claims(self.name, str(claims["iss"]), claims), claims
