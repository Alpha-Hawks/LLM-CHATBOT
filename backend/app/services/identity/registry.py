"""
Identity Provider Registry.
Selects the official Anvaya identity mechanism from IDENTITY_PROVIDER. Returns None when no
mechanism is configured, in which case personal records are simply unavailable.
"""

from typing import Dict, Optional

from backend.app.core.config import settings
from backend.app.services.identity.base import IdentityProvider
from backend.app.services.identity.launch_token import SignedLaunchTokenProvider
from backend.app.services.identity.oidc import OIDCProvider

class AnvayaSSOIdentityProvider(IdentityProvider):
    name = "anvaya_sso"

    def is_configured(self) -> bool:
        return bool(settings.ANVAYA_BASE_URL)


# One instance per mechanism so key/discovery caches are shared; tests may replace entries
providers: Dict[str, IdentityProvider] = {
    SignedLaunchTokenProvider.name: SignedLaunchTokenProvider(),
    OIDCProvider.name: OIDCProvider(),
    AnvayaSSOIdentityProvider.name: AnvayaSSOIdentityProvider(),
}


def get_identity_provider() -> Optional[IdentityProvider]:
    provider = providers.get(settings.IDENTITY_PROVIDER.strip().lower())
    if provider is None or not provider.is_configured():
        return None
    return provider
