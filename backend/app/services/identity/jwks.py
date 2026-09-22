"""
Signing-key retrieval for identity assertions.
Fetches a JSON Web Key Set only from a URL that the college / identity provider configured,
caches it, and refreshes once when an unknown key ID appears (key rotation).
"""

import time
from typing import Any, Dict, Optional, Tuple
import httpx
import jwt

from backend.app.core.config import settings
from backend.app.services.identity.base import IdentityError

CACHE_TTL_SECONDS = 3600


def require_https(url: str, what: str) -> None:
    """Identity metadata must travel over HTTPS outside development."""
    if not url.lower().startswith("https://") and settings.ENVIRONMENT != "development":
        raise IdentityError(f"{what} must use https:// ({url}).")


async def fetch_json(url: str, what: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> Dict[str, Any]:
    require_https(url, what)
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise IdentityError(f"Could not fetch {what} ({type(e).__name__}).")
    if resp.status_code != 200:
        raise IdentityError(f"Could not fetch {what} (HTTP {resp.status_code}).")
    try:
        data = resp.json()
    except ValueError:
        raise IdentityError(f"{what} is not valid JSON.")
    if not isinstance(data, dict):
        raise IdentityError(f"{what} is not a JSON object.")
    return data


class JwksCache:
    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._transport = transport
        self._cache: Dict[str, Tuple[float, jwt.PyJWKSet]] = {}

    async def _load(self, url: str, force: bool) -> jwt.PyJWKSet:
        cached = self._cache.get(url)
        if cached and not force and time.time() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        data = await fetch_json(url, "JWKS", self._transport)
        try:
            key_set = jwt.PyJWKSet.from_dict(data)
        except jwt.PyJWTError:
            raise IdentityError("JWKS contains no usable signing keys.")
        self._cache[url] = (time.time(), key_set)
        return key_set

    async def signing_key(self, url: str, kid: Optional[str]) -> Any:
        for force in (False, True):
            key_set = await self._load(url, force)
            keys = [k for k in key_set.keys if (k.public_key_use in (None, "sig"))]
            if kid is None and len(keys) == 1:
                return keys[0].key
            for k in keys:
                if kid is not None and k.key_id == kid:
                    return k.key
        raise IdentityError("Signing key for this assertion was not found in the JWKS.")
