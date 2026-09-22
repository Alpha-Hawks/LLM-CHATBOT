"""
Student Profile Store  (Anvaya Auth -> Student Data Service -> **Profile Store** -> Context Engine -> AI).

The chatbot's own copy of ONE student's authorized academic snapshot, so the assistant can answer
without calling Anvaya on every message. It is deliberately not a copy of Anvaya's database:
a row only ever exists for a student who signed in, and it holds only what the official interface
released for that student.

Isolation rules enforced here:
- `student_key` (set server-side from the verified Anvaya identity) is the only key any read or
  write is scoped by. Nothing accepts a roll number or key from the browser.
- A stored snapshot whose own `student_key` does not match the caller's session is discarded and
  reported, never returned. Student A therefore cannot be served Student B's row even if the
  table were tampered with.
- The academic payload is encrypted at rest with AES-256-GCM (core/security.py), so a database
  file or backup on its own does not expose attendance, marks or results.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.security import decrypt_session_payload, encrypt_session_payload
from backend.app.db.models import StudentProfile
from backend.app.services.identity.base import StudentContext
from backend.app.services.student_data.schemas import StudentAcademicProfile

logger = logging.getLogger(__name__)


class StoredProfile(BaseModel):
    """A snapshot read back from the store, with its freshness and where it came from."""

    profile: StudentAcademicProfile
    last_synced_at: datetime
    source: str
    provider: Optional[str] = None

    @property
    def age_seconds(self) -> float:
        return max(0.0, (datetime.utcnow() - self.last_synced_at).total_seconds())


async def load(db: AsyncSession, student: StudentContext) -> Optional[StoredProfile]:
    """The signed-in student's stored snapshot, or None when there is none to serve."""
    row = (
        await db.execute(select(StudentProfile).where(StudentProfile.student_key == student.student_key))
    ).scalar_one_or_none()
    if row is None:
        return None

    payload = decrypt_session_payload(row.profile_data_json)
    if payload is None:
        # Wrong key, or the row was altered: refuse it rather than show doubtful records
        logger.error(f"Stored profile for session {student.session_id} failed decryption; discarding it.")
        return None

    try:
        profile = StudentAcademicProfile(**payload)
    except Exception:
        logger.error(f"Stored profile for session {student.session_id} no longer matches the schema; discarding it.")
        return None

    if profile.student_key != student.student_key or row.student_key != student.student_key:
        logger.error(
            f"Blocked a stored profile whose owner does not match session {student.session_id}. Nothing returned."
        )
        return None

    return StoredProfile(
        profile=profile,
        last_synced_at=row.last_synced_at,
        source=row.source or profile.source,
        provider=row.provider,
    )


async def _row_for_update(db: AsyncSession, student: StudentContext) -> StudentProfile:
    """This student's row, inserting one if needed. Tolerates a concurrent worker inserting first."""
    row = (
        await db.execute(select(StudentProfile).where(StudentProfile.student_key == student.student_key))
    ).scalar_one_or_none()
    if row is not None:
        return row

    row = StudentProfile(student_key=student.student_key, profile_data_json="")
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        row = (
            await db.execute(select(StudentProfile).where(StudentProfile.student_key == student.student_key))
        ).scalar_one()
    return row


async def save(
    db: AsyncSession,
    student: StudentContext,
    profile: StudentAcademicProfile,
    provider: Optional[str] = None,
) -> StoredProfile:
    """Writes (or replaces) the snapshot for the signed-in student only."""
    if profile.student_key != student.student_key:
        # A provider that returned someone else's record must never be persisted
        logger.error(f"Refused to store a profile that does not belong to session {student.session_id}.")
        raise PermissionError("Refused to store a profile belonging to a different student.")

    now = datetime.utcnow()
    profile.last_synced = now
    encrypted = encrypt_session_payload(json.loads(profile.model_dump_json()))

    row = await _row_for_update(db, student)
    row.roll_number = profile.roll_number
    row.anvaya_user_id = profile.anvaya_user_id or student.anvaya_user_id or student.subject
    row.student_id = profile.student_id or student.student_id or profile.roll_number
    row.display_name = profile.display_name
    row.department = profile.department
    row.course = profile.course
    row.year = profile.year
    row.current_semester = profile.current_semester
    row.section = profile.section
    row.academic_year = profile.academic_year
    row.email = profile.email
    row.profile_data_json = encrypted
    row.provider = provider
    row.last_synced_at = now
    row.source = profile.source
    await db.commit()

    return StoredProfile(profile=profile, last_synced_at=now, source=profile.source, provider=provider)


async def purge(db: AsyncSession, student: StudentContext) -> None:
    """Deletes the signed-in student's stored snapshot (DPDP Act 2023 erasure / consent withdrawal)."""
    await db.execute(delete(StudentProfile).where(StudentProfile.student_key == student.student_key))
    await db.commit()
