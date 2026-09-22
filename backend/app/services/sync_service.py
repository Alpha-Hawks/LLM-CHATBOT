"""
Controlled Profile Synchronization.

Bridges the Student Data Service and the Profile Store. It never mirrors Anvaya's database: one
signed-in student, one authorized snapshot, one row.

    stored snapshot fresh (< PROFILE_SYNC_TTL_SECONDS)   -> serve it                    "cached"
    stale or forced                                      -> re-read from Anvaya         "fresh"
    Anvaya unreachable but a recent snapshot exists      -> serve it, stamped as stale  "stale"
    Anvaya unreachable and nothing stored                -> say so, invent nothing      "unavailable"
    no official interface configured                     -> say so, invent nothing      "not_configured"

Because attendance, marks and timetables change during a semester, a snapshot older than the TTL
is refreshed on the next question, and `/api/v1/student/sync` forces a refresh on demand. When the
refreshed snapshot reports a different semester, `semester_changed` is set so the current-semester
context follows the student into the new semester automatically.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.services import audit, student_profile_store
from backend.app.services.authorization import StudentAccessDenied, verify_record_owner
from backend.app.services.identity.base import StudentContext
from backend.app.services.student_data.base import StudentDataNotConfigured, StudentDataUnavailable
from backend.app.services.student_data.providers import get_student_data_provider
from backend.app.services.student_data.schemas import StudentAcademicProfile

logger = logging.getLogger(__name__)

# Serializes concurrent syncs for the same student within this process
_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(student_key: str) -> asyncio.Lock:
    lock = _locks.get(student_key)
    if lock is None:
        lock = _locks.setdefault(student_key, asyncio.Lock())
    return lock


class SyncOutcome(BaseModel):
    """What the assistant is allowed to use for this student right now, and how fresh it is."""

    status: str                       # fresh | cached | stale | unavailable | not_configured
    profile: Optional[StudentAcademicProfile] = None
    last_synced_at: Optional[datetime] = None
    source: Optional[str] = None
    message: Optional[str] = None     # student-facing explanation when nothing usable is available
    semester_changed: bool = False

    @property
    def has_data(self) -> bool:
        return self.profile is not None

    @property
    def is_stale(self) -> bool:
        return self.status == "stale"

    @property
    def data_status(self) -> str:
        if self.status == "fresh":
            return "Live"
        if self.status == "cached":
            return "Recently Synced"
        if self.status == "stale":
            return "Stale (Offline)"
        return "Unavailable"

    def age_seconds(self) -> Optional[float]:
        if self.last_synced_at is None:
            return None
        return max(0.0, (datetime.utcnow() - self.last_synced_at).total_seconds())


def derive_current_semester(profile: StudentAcademicProfile) -> Optional[int]:
    """
    The student's current semester, taken from the authorized data only.

    Anvaya's own value wins. If it does not supply one, the highest semester across the
    student's enrolled subjects is used, since those are this semester's registrations. When
    neither exists the answer is None and the assistant says the semester is not available -
    it is never guessed from the year or the date.
    """
    if profile.current_semester:
        return profile.current_semester

    semesters = [s.semester for s in profile.current_subjects if s.semester]
    return max(semesters) if semesters else None


async def _fetch_authorized_profile(student: StudentContext) -> StudentAcademicProfile:
    """
    Reads this student's snapshot from the official interface and re-checks who owns it.

    The ownership check is the authorization layer's, not this module's, and it covers the
    snapshot and each record nested inside it.
    """
    profile = await get_student_data_provider().get_profile(student)

    verify_record_owner(profile, student, "synced profile")
    for record in (profile.attendance, profile.semester_results, profile.timetable):
        if record is not None:
            verify_record_owner(record, student, "synced sub-record")

    profile.current_semester = derive_current_semester(profile)
    return profile


async def get_profile(
    db: AsyncSession,
    student: StudentContext,
    *,
    force: bool = False,
    prefer_fresh: bool = False,
) -> SyncOutcome:
    """
    The authorized snapshot for the signed-in student, refreshed when it has gone stale.

    The caller must already have passed authorization (session + DPDP consent); this function
    decides freshness, not permission.
    """
    async with _lock_for(student.student_key):
        provider = get_student_data_provider()
        stored = await student_profile_store.load(db, student)

        # A snapshot belongs to the interface that produced it. If the configured source has
        # changed - development samples switched off, or the official API connected - the old
        # snapshot is not evidence about the new one and is never served.
        if stored and stored.provider not in (None, provider.name):
            logger.info(
                f"Discarding a stored snapshot from provider {stored.provider!r}; "
                f"the configured source is now {provider.name!r}."
            )
            stored = None

        # No interface at all is not the same as an interface that is temporarily down: with
        # nothing connected there is nothing legitimate to serve, however recent the copy.
        if provider.name == "none":
            return await _fall_back(
                db, student, None, status="not_configured",
                message=(
                    "Personal academic records are not connected yet. MLRITM / ORGMAKER has not "
                    "provided an official data interface for this assistant."
                ),
            )

        dynamic_threshold = min(60, settings.PROFILE_SYNC_TTL_SECONDS) if prefer_fresh else settings.PROFILE_SYNC_TTL_SECONDS
        if stored and not force and stored.age_seconds < dynamic_threshold:
            return SyncOutcome(
                status="cached",
                profile=stored.profile,
                last_synced_at=stored.last_synced_at,
                source=stored.source,
            )

        previous_semester = stored.profile.current_semester if stored else None

        try:
            profile = await _fetch_authorized_profile(student)
        except StudentAccessDenied:
            # An ownership failure is a security event, not a freshness problem: it must reach
            # the caller as a refusal instead of quietly falling back to the stored snapshot.
            await audit.record(
                db, "RECORD_ACCESS", student=student, status="BLOCKED",
                data_category="profile", detail="ownership_mismatch",
            )
            raise
        except StudentDataNotConfigured as e:
            # Same reasoning as above: nothing connected means nothing to serve
            return await _fall_back(db, student, None, status="not_configured", message=str(e))
        except StudentDataUnavailable as e:
            return await _fall_back(db, student, stored, status="unavailable", message=str(e))
        except Exception as e:
            logger.error(f"Profile sync failed for session {student.session_id} ({type(e).__name__}).")
            return await _fall_back(
                db, student, stored, status="unavailable",
                message="I could not reach Anvaya to load your records just now.",
            )

        saved = await student_profile_store.save(db, student, profile, provider=provider.name)
        semester_changed = (
            previous_semester is not None
            and profile.current_semester is not None
            and profile.current_semester != previous_semester
        )
        await audit.record(
            db, "PROFILE_SYNC", student=student, data_category="profile",
            detail=f"semester={profile.current_semester}" + (" (changed)" if semester_changed else ""),
        )
        if semester_changed:
            logger.info(f"Session {student.session_id}: current semester moved to {profile.current_semester}.")

        return SyncOutcome(
            status="fresh",
            profile=saved.profile,
            last_synced_at=saved.last_synced_at,
            source=saved.source,
            semester_changed=semester_changed,
        )


async def _fall_back(db: AsyncSession, student: StudentContext, stored, *, status: str, message: str) -> SyncOutcome:
    """
    Anvaya could not be read. Serve the last authorized snapshot if it is still recent enough,
    clearly marked as stale, otherwise report the gap. Nothing is ever invented to fill it.
    """
    await audit.record(db, "PROFILE_SYNC", student=student, status="FAILED", data_category="profile", detail=status)

    if stored and stored.age_seconds <= settings.PROFILE_MAX_STALE_SECONDS:
        return SyncOutcome(
            status="stale",
            profile=stored.profile,
            last_synced_at=stored.last_synced_at,
            source=stored.source,
            message=message,
        )
    return SyncOutcome(status=status, message=message)
