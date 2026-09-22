"""
Student Data Authorization (backend only).

The single gate for student-specific records. Every decision uses the server-side session and
nothing else:

    no valid session           -> not_signed_in
    no DPDP consent recorded   -> consent_required
    record owner != session    -> ownership_mismatch  (logged, nothing returned)

The intent router and the language model only choose WHICH category of the signed-in student's
own records to show. They never supply an identity, they are never consulted for access
decisions, and their output reaches no query before this module has approved it.

Ownership is re-checked at every boundary a record crosses: when it arrives from the provider
(sync_service), when it is written to or read from the profile store, and here before a typed
record is returned. A provider that returned the wrong student's record therefore cannot reach
a response formatter, a prompt, or a student.
"""

import logging
from typing import Any, Optional, Union

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.services import sessions
from backend.app.services.identity.base import StudentContext
from backend.app.services.student_data.providers import get_student_data_provider
from backend.app.services.student_data.schemas import AttendanceRecord, SemesterResult, TimetableRecord

logger = logging.getLogger(__name__)

StudentRecord = Union[AttendanceRecord, SemesterResult, TimetableRecord]

# Question category -> the provider call that loads it for the signed-in student
RECORD_LOADERS = {
    "attendance": "get_attendance",
    "results": "get_results",
    "timetable": "get_timetable",
    "subjects": "get_subjects",
    "faculty": "get_faculty",
    "marks": "get_internal_marks",
    "exams": "get_exams",
    "assignments": "get_assignments",
    "backlogs": "get_backlogs",
    "profile": "get_profile",
}


class StudentAccessDenied(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):].strip()
        return token or None
    return None


async def get_student_context(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> Optional[StudentContext]:
    """FastAPI dependency: the signed-in student for this request, or None."""
    row = await sessions.resolve_session(db, bearer_token(authorization))
    if not row:
        return None
    return StudentContext(
        session_id=row.id,
        subject=row.subject,
        student_key=row.student_key,
        roll_number=row.roll_number,
        display_name=row.display_name,
        consent_granted=row.consent_granted_at is not None,
        anvaya_user_id=row.anvaya_user_id,
        student_id=row.student_id,
    )


def ensure_can_read_records(student: Optional[StudentContext]) -> StudentContext:
    """
    The permission check that must pass before ANY personal data is loaded.

    Called at the top of every path that reads student records - chat, the quick-action
    endpoints and the synchronization endpoint - so no route can reach the data service
    without it.
    """
    if student is None:
        raise StudentAccessDenied("not_signed_in")
    if not student.consent_granted:
        raise StudentAccessDenied("consent_required")
    return student


def verify_record_owner(record: Any, student: StudentContext, what: str = "record") -> Any:
    """
    Confirms a record belongs to the signed-in student, or refuses it.

    Used wherever data crosses into the application from a provider or the store. A list is
    checked element by element; anything without a `student_key` (a subject row, a faculty
    entry) is carried by the profile whose ownership was already checked.
    """
    items = record if isinstance(record, list) else [record]
    for item in items:
        owner = getattr(item, "student_key", None)
        if owner is not None and owner != student.student_key:
            logger.error(
                f"Blocked a {what} whose owner does not match session {student.session_id}. Nothing returned."
            )
            raise StudentAccessDenied("ownership_mismatch")
    return record


async def fetch_authorized_record(student: Optional[StudentContext], category: str) -> StudentRecord:
    """
    Loads one category of the signed-in student's OWN records after all backend checks pass.

    Used by the quick-action endpoints, which want a single typed record rather than a
    question-shaped context bundle.
    """
    authorized = ensure_can_read_records(student)
    loader = RECORD_LOADERS[category]
    record = await getattr(get_student_data_provider(), loader)(authorized)
    return verify_record_owner(record, authorized, f"{category} record")
