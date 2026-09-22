"""
Chatbot Session Service.

Creates the chatbot's own session only from an AuthenticatedIdentity that an identity provider
verified. Raw tokens are random (secrets.token_urlsafe) and exist only in the browser; the
database keeps their SHA-256 hashes.

Handoff: after a successful launch/callback the browser is redirected with a one-time code in
the URL fragment (never sent to servers). The page exchanges it once, within a minute, for the
session token, so a URL left in history or logs cannot be reused.
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.models import AuthFlowState, ChatSession, StudentConsent, UsedLaunchToken
from backend.app.services.identity.base import AuthenticatedIdentity, IdentityError

HANDOFF_TTL_SECONDS = 60
AUTH_FLOW_TTL_SECONDS = 600


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def create_pending_session(db: AsyncSession, identity: AuthenticatedIdentity) -> str:
    """Stores a session for the verified identity and returns its one-time handoff code."""
    now = datetime.utcnow()
    code = secrets.token_urlsafe(32)
    clean_roll = (identity.roll_number or identity.student_key or "").strip().upper()

    existing_consent = None
    if clean_roll:
        existing_consent = (await db.execute(
            select(StudentConsent).where(
                StudentConsent.roll_number == clean_roll,
                StudentConsent.consent_given == True
            )
        )).scalars().first()

    consent_time = now if (existing_consent or identity.provider in ("anvaya_sso", "anvaya")) else None

    db.add(ChatSession(
        handoff_hash=_hash(code),
        handoff_expires_at=now + timedelta(seconds=HANDOFF_TTL_SECONDS),
        identity_provider=identity.provider,
        issuer=identity.issuer,
        subject=identity.subject,
        student_key=identity.student_key,
        roll_number=identity.roll_number,
        display_name=identity.display_name,
        anvaya_user_id=identity.anvaya_user_id,
        student_id=identity.student_id,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.SESSION_TTL_MINUTES),
        consent_granted_at=consent_time,
    ))
    if consent_time and not existing_consent and clean_roll:
        db.add(StudentConsent(roll_number=clean_roll, consent_given=True, consent_timestamp=now))
    await db.commit()
    return code


async def exchange_handoff(db: AsyncSession, code: str) -> Optional[Tuple[str, ChatSession]]:
    """Swaps a handoff code for the session token exactly once. Returns None if invalid."""
    if not code:
        return None
    now = datetime.utcnow()
    token = secrets.token_urlsafe(32)
    code_hash = _hash(code)
    result = await db.execute(
        update(ChatSession)
        .where(
            ChatSession.handoff_hash == code_hash,
            ChatSession.token_hash.is_(None),
            ChatSession.handoff_expires_at > now,
            ChatSession.expires_at > now,
            ChatSession.revoked_at.is_(None),
        )
        .values(token_hash=_hash(token), handoff_hash=None, handoff_expires_at=None)
    )
    await db.commit()
    if result.rowcount != 1:
        return None
    row = (await db.execute(select(ChatSession).where(ChatSession.token_hash == _hash(token)))).scalar_one()
    return token, row


async def resolve_session(db: AsyncSession, token: str) -> Optional[ChatSession]:
    if not token:
        return None
    row = (await db.execute(
        select(ChatSession).where(ChatSession.token_hash == _hash(token))
    )).scalar_one_or_none()
    if not row or row.revoked_at is not None or row.expires_at <= datetime.utcnow():
        return None
    return row


async def revoke_session(db: AsyncSession, token: str) -> None:
    if not token:
        return
    await db.execute(
        update(ChatSession)
        .where(ChatSession.token_hash == _hash(token), ChatSession.revoked_at.is_(None))
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()


async def grant_consent(db: AsyncSession, session_row: ChatSession) -> None:
    now = datetime.utcnow()
    await db.execute(update(ChatSession).where(ChatSession.id == session_row.id).values(consent_granted_at=now))
    db.add(StudentConsent(roll_number=session_row.student_key, consent_given=True, consent_timestamp=now))
    await db.commit()


async def consume_launch_token_id(db: AsyncSession, issuer: str, jti: str, expires_at: datetime) -> None:
    """Accepts each launch token ID once; a second use raises IdentityError (replay)."""
    now = datetime.utcnow()
    await db.execute(delete(UsedLaunchToken).where(UsedLaunchToken.expires_at < now - timedelta(days=1)))
    db.add(UsedLaunchToken(issuer=issuer, jti=jti, expires_at=expires_at))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise IdentityError("Launch token has already been used.")


async def create_auth_flow(db: AsyncSession) -> Tuple[str, str, str]:
    """Returns (state, nonce, code_verifier) for an OIDC sign-in; stores them server-side."""
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    db.add(AuthFlowState(
        state_hash=_hash(state),
        nonce=nonce,
        code_verifier=verifier,
        expires_at=datetime.utcnow() + timedelta(seconds=AUTH_FLOW_TTL_SECONDS),
    ))
    await db.commit()
    return state, nonce, verifier


async def consume_auth_flow(db: AsyncSession, state: str) -> Optional[Tuple[str, str]]:
    """Marks the OIDC state used exactly once; returns (nonce, code_verifier) or None."""
    if not state:
        return None
    now = datetime.utcnow()
    state_hash = _hash(state)
    result = await db.execute(
        update(AuthFlowState)
        .where(AuthFlowState.state_hash == state_hash, AuthFlowState.used_at.is_(None), AuthFlowState.expires_at > now)
        .values(used_at=now)
    )
    await db.commit()
    if result.rowcount != 1:
        return None
    row = (await db.execute(select(AuthFlowState).where(AuthFlowState.state_hash == state_hash))).scalar_one()
    return row.nonce, row.code_verifier


async def create_anvaya_auth_code(db: AsyncSession, roll_number: str, display_name: str) -> Tuple[str, str]:
    """Creates a short-lived single-use authorization code for the Anvaya SSO gateway."""
    code = secrets.token_urlsafe(32)
    state = secrets.token_urlsafe(16)
    db.add(AuthFlowState(
        state_hash=_hash(code),
        nonce=roll_number,
        code_verifier=display_name,
        expires_at=datetime.utcnow() + timedelta(seconds=120),
    ))
    await db.commit()
    return code, state


async def consume_anvaya_auth_code(db: AsyncSession, code: str) -> Optional[Tuple[str, str]]:
    """Consumes the Anvaya SSO authorization code exactly once, returning (roll_number, display_name)."""
    if not code:
        return None
    now = datetime.utcnow()
    code_hash = _hash(code)
    result = await db.execute(
        update(AuthFlowState)
        .where(AuthFlowState.state_hash == code_hash, AuthFlowState.used_at.is_(None), AuthFlowState.expires_at > now)
        .values(used_at=now)
    )
    await db.commit()
    if result.rowcount != 1:
        return None
    row = (await db.execute(select(AuthFlowState).where(AuthFlowState.state_hash == code_hash))).scalar_one()
    return row.nonce, row.code_verifier
