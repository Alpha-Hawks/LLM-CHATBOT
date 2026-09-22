"""
Audit logging for sensitive operations.

Records that something happened to whose records, never what the records said. The student is
identified by `student_ref`, a truncated SHA-256 of the server-side student key, so the audit
trail can follow one student's activity without becoming a second register of who the students
are. Nothing here writes a token, a question, a name or a record value.
"""

import hashlib
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AuditLog
from backend.app.services.identity.base import StudentContext

logger = logging.getLogger(__name__)


def student_ref(student: Optional[StudentContext]) -> Optional[str]:
    """Pseudonymous, stable reference to a student for audit correlation."""
    if student is None:
        return None
    return hashlib.sha256(student.student_key.encode("utf-8")).hexdigest()[:32]


async def record(
    db: AsyncSession,
    event_type: str,
    *,
    student: Optional[StudentContext] = None,
    intent: Optional[str] = None,
    data_category: Optional[str] = None,
    status: str = "SUCCESS",
    detail: Optional[str] = None,
    latency_ms: int = 0,
    commit: bool = True,
) -> None:
    """Appends one audit entry. Auditing must never break the student's answer, so it fails soft."""
    try:
        db.add(AuditLog(
            event_type=event_type[:50],
            intent_detected=(intent or None) and intent[:50],
            response_status=status[:20],
            latency_ms=latency_ms,
            student_ref=student_ref(student),
            data_category=(data_category or None) and data_category[:50],
            detail=(detail or None) and detail[:255],
        ))
        if commit:
            await db.commit()
    except Exception as e:
        logger.warning(f"Could not write audit entry {event_type} ({type(e).__name__}).")
