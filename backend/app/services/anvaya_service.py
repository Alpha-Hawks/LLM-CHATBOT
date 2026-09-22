"""
Official Anvaya ERP Integration Service.

Coordinates authentication, student identity verification, session scoping,
and academic records synchronization with the MLRITM Anvaya Portal:
    - Login: https://anvaya.mlritm.ac.in/Login
    - Authenticated App: https://anvaya.mlritm.ac.in/App

Zero Default Student Details Policy:
    - Never displays, logs, or defaults to hardcoded student identities (e.g. 237Y1A1270).
    - Student identity is derived solely from verified Anvaya authentication results.
    - DPDP Act 2023 compliant: student passwords are never stored or logged.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.services import student_profile_store, sync_service
from backend.app.services.identity.base import AuthenticatedIdentity, StudentContext
from backend.app.services.student_data.base import StudentDataUnavailable
from backend.app.services.student_data.schemas import StudentAcademicProfile

logger = logging.getLogger(__name__)


class AnvayaService:
    """Production service and adapter for the official MLRITM Anvaya portal."""

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._transport = transport

    @property
    def base_url(self) -> str:
        return settings.ANVAYA_BASE_URL.rstrip("/")

    @property
    def login_url(self) -> str:
        return getattr(settings, "ANVAYA_LOGIN_URL", f"{self.base_url}/Login")

    @property
    def app_url(self) -> str:
        return getattr(settings, "ANVAYA_APP_URL", f"{self.base_url}/App")

    def extract_student_identity(
        self,
        roll_number: str,
        name: Optional[str] = None,
        anvaya_user_id: Optional[str] = None,
        student_id: Optional[str] = None,
    ) -> AuthenticatedIdentity:
        """
        Derives the verified student identity using the strongest identifiers available:
            1. anvaya_user_id
            2. student_id
            3. roll_number

        Refuses to identify students by name alone.
        """
        clean_roll = str(roll_number or "").strip().upper()
        if not clean_roll:
            raise ValueError("Roll number or valid student identifier is mandatory.")

        uid = anvaya_user_id or f"anvaya-{clean_roll.lower()}"
        sid = student_id or clean_roll
        display = (name or f"Student ({clean_roll})").strip()

        return AuthenticatedIdentity(
            provider="anvaya_sso",
            issuer="anvaya.mlritm.ac.in",
            subject=uid,
            student_key=uid,
            roll_number=clean_roll,
            display_name=display,
            anvaya_user_id=uid,
            student_id=sid,
        )

    async def synchronize_student(
        self,
        db: AsyncSession,
        student: StudentContext,
        force: bool = False,
    ) -> sync_service.SyncOutcome:
        """
        Synchronizes the student's authorized academic information from Anvaya,
        validates student identity scoping, and securely caches the encrypted snapshot.
        """
        return await sync_service.get_profile(db, student, force=force)

    async def get_authorized_profile(
        self,
        db: AsyncSession,
        student: StudentContext,
    ) -> Optional[StudentAcademicProfile]:
        """Loads the authorized student academic profile for the verified session."""
        outcome = await sync_service.get_profile(db, student, force=False)
        if outcome.has_data:
            return outcome.profile
        return None


# Shared singleton instance
anvaya_service = AnvayaService()
