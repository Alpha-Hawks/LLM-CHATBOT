"""
Identity Integration Contracts.

Anvaya (the college ERP) is the only source of student identity. The chatbot never asks for,
receives, or stores an Anvaya password, and never lets the browser choose whose records to load.

Flow:
    Anvaya-issued assertion (signed launch token / OIDC ID token)
        -> IdentityProvider verifies it                  -> AuthenticatedIdentity
        -> sessions service creates the chatbot session   (server-side, hashed token)
        -> authorization layer rebuilds StudentContext   from the session on every request
        -> student data provider is called with that StudentContext only
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from backend.app.core.config import settings


class IdentityError(Exception):
    """The identity assertion is missing, invalid, expired, replayed, or not a student."""


class IdentityNotConfigured(IdentityError):
    """No official Anvaya identity mechanism has been configured yet."""


class AuthenticatedIdentity(BaseModel):
    """An identity verified from an Anvaya-issued assertion. Never built from browser input."""
    provider: str
    issuer: str
    subject: str
    student_key: str
    roll_number: Optional[str] = None
    display_name: Optional[str] = None
    anvaya_user_id: Optional[str] = None
    student_id: Optional[str] = None


class StudentContext(BaseModel):
    """
    The signed-in student for the current request, rebuilt from the server-side session.
    This is the ONLY input student data providers accept to decide whose records to load.
    """
    session_id: int
    subject: str
    student_key: str
    roll_number: Optional[str] = None
    display_name: Optional[str] = None
    consent_granted: bool = False
    anvaya_user_id: Optional[str] = None
    student_id: Optional[str] = None


class IdentityProvider(ABC):
    name: str = "none"

    @abstractmethod
    def is_configured(self) -> bool:
        """True only when every setting the official mechanism needs is present."""


def _claim_str(claims: Dict[str, Any], name: str) -> Optional[str]:
    if not name:
        return None
    value = claims.get(name)
    if value is None or isinstance(value, (dict, list)):
        return None
    value = str(value).strip()
    return value or None


def identity_from_claims(provider: str, issuer: str, claims: Dict[str, Any]) -> AuthenticatedIdentity:
    """
    Maps VERIFIED claims to an identity using server-side configuration only.
    Enforces the optional student-role restriction so staff or parent accounts are refused.
    """
    subject = _claim_str(claims, "sub")
    if not subject:
        raise IdentityError("Identity assertion has no subject (sub) claim.")

    student_key = _claim_str(claims, settings.IDENTITY_STUDENT_KEY_CLAIM)
    if not student_key:
        raise IdentityError(f"Identity assertion is missing the '{settings.IDENTITY_STUDENT_KEY_CLAIM}' claim.")

    if settings.IDENTITY_ROLE_CLAIM:
        allowed: List[str] = [v.strip().lower() for v in settings.IDENTITY_STUDENT_ROLE_VALUES.split(",") if v.strip()]
        raw_role = claims.get(settings.IDENTITY_ROLE_CLAIM)
        roles = [str(r).strip().lower() for r in (raw_role if isinstance(raw_role, list) else [raw_role]) if r is not None]
        if not allowed or not any(r in allowed for r in roles):
            raise IdentityError("This Anvaya account is not a student account.")

    roll_number = _claim_str(claims, settings.IDENTITY_ROLL_NUMBER_CLAIM) or (subject.upper() if subject else None)
    anvaya_user_id = _claim_str(claims, "anvaya_user_id") or subject
    student_id = _claim_str(claims, "student_id") or roll_number or student_key

    return AuthenticatedIdentity(
        provider=provider,
        issuer=issuer,
        subject=subject,
        student_key=student_key,
        roll_number=roll_number,
        display_name=_claim_str(claims, settings.IDENTITY_NAME_CLAIM),
        anvaya_user_id=anvaya_user_id,
        student_id=student_id,
    )
