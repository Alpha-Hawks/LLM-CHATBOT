"""
Student Profile & Quick Action API.

Everything here answers for the signed-in student and nobody else. Each route calls
`ensure_can_read_records` before it touches the data service, and no route accepts a roll
number, a student key or any other identifier from the browser: the only identity in play is
the one held in the server-side session.

    GET    /student/profile        identity, current semester and freshness (drives the header)
    POST   /student/sync           force a refresh from Anvaya
    GET    /student/record/{kind}  one category, as data plus ready-to-render Markdown
    DELETE /student/data           erase the stored snapshot (DPDP Act 2023)
"""

import logging
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.services import audit, context_engine, student_profile_store, sync_service
from backend.app.services.authorization import StudentAccessDenied, ensure_can_read_records, get_student_context
from backend.app.services.identity.base import StudentContext
from backend.app.services.response_formatter import format_context_bundle
from backend.app.services.student_service import getStudentByRollNumber
from backend.app.llm.intent_router import IntentEnum

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/student", tags=["Student Profile & Records"])


class RecordKind(str, Enum):
    """The categories a student can ask for directly. Nothing outside this list is reachable."""

    attendance = "attendance"
    subjects = "subjects"
    marks = "marks"
    faculty = "faculty"
    timetable = "timetable"
    exams = "exams"
    results = "results"
    assignments = "assignments"
    profile = "profile"
    notifications = "notifications"


# Quick action -> (intent used to select fields, the question the context engine sees)
RECORD_INTENTS = {
    RecordKind.attendance: (IntentEnum.ATTENDANCE, "What is my attendance?"),
    RecordKind.subjects: (IntentEnum.SUBJECTS, "Which subjects am I studying this semester?"),
    RecordKind.marks: (IntentEnum.MARKS, "What are my internal marks?"),
    RecordKind.faculty: (IntentEnum.FACULTY, "Who are my faculty members?"),
    RecordKind.timetable: (IntentEnum.TIMETABLE, "What is my class timetable?"),
    RecordKind.exams: (IntentEnum.EXAMS, "What is my examination schedule?"),
    RecordKind.results: (IntentEnum.RESULTS, "What are my latest results?"),
    RecordKind.assignments: (IntentEnum.ASSIGNMENTS, "What are my assignments?"),
    RecordKind.profile: (IntentEnum.PROFILE, "Show my academic profile."),
    RecordKind.notifications: (IntentEnum.PROFILE, "What are my academic notifications?"),
}

DENIED_STATUS = {
    "not_signed_in": (status.HTTP_401_UNAUTHORIZED, "Sign in with your Anvaya account to see your records."),
    "consent_required": (status.HTTP_403_FORBIDDEN, "Please give DPDP Act 2023 consent before viewing your records."),
    "ownership_mismatch": (status.HTTP_403_FORBIDDEN, "That record did not belong to your account."),
}


def _authorized(student: Optional[StudentContext]) -> StudentContext:
    try:
        return ensure_can_read_records(student)
    except StudentAccessDenied as denied:
        code, message = DENIED_STATUS[denied.reason]
        raise HTTPException(status_code=code, detail=message)


def _profile_payload(outcome: sync_service.SyncOutcome) -> dict:
    """
    The header payload. Built from an explicit field list so the student key, the session and
    the stored academic detail never travel to the browser with it.
    """
    if not outcome.has_data:
        return {
            "connected": False,
            "status": outcome.status,
            "message": outcome.message,
            "anvaya_url": settings.ANVAYA_BASE_URL,
        }

    profile = outcome.profile
    return {
        "connected": True,
        "status": outcome.status,
        "is_stale": outcome.is_stale,
        "message": outcome.message,
        "identity": context_engine.identity_summary(profile),
        "current_semester": profile.current_semester,
        "semester_changed": outcome.semester_changed,
        "counts": {
            "subjects": len(profile.current_subjects),
            "faculty": len(profile.faculty_list),
            "upcoming_exams": len(profile.upcoming_exams),
            "pending_assignments": sum(1 for a in profile.assignments if (a.status or "").lower() == "pending"),
            "backlogs": len(profile.backlogs),
            "notifications": len(getattr(profile, "notifications", [])),
        },
        "attendance_summary": {
            "overall_percentage": profile.attendance.aggregate_percentage,
            "below_required_75": profile.attendance.is_shortage,
            "detention_risk": profile.attendance.detention_risk,
            "classes_needed_to_reach_75": profile.attendance.classes_needed_for_75,
        } if profile.attendance else None,
        "latest_result_summary": {
            "semester": profile.semester_results.semester,
            "sgpa": profile.semester_results.sgpa,
            "cgpa": profile.semester_results.cgpa,
            "backlogs": profile.semester_results.backlogs_count,
        } if profile.semester_results else None,
        "last_synced_at": outcome.last_synced_at,
        "last_synced_formatted": outcome.last_synced_at.strftime("%d-%b-%Y %I:%M %p") if outcome.last_synced_at else None,
        "data_status": outcome.data_status,
        "source": outcome.source,
        "anvaya_url": settings.ANVAYA_BASE_URL,
    }


@router.get("/profile")
async def student_profile(
    student: Optional[StudentContext] = Depends(get_student_context),
    db: AsyncSession = Depends(get_db),
):
    """The signed-in student's academic header: name, department, current semester, freshness."""
    authorized = _authorized(student)
    outcome = await sync_service.get_profile(db, authorized)
    payload = _profile_payload(outcome)

    roll = (authorized.roll_number or authorized.subject or "").strip().upper()
    stored_student = await getStudentByRollNumber(roll, session=db)
    if isinstance(stored_student, dict):
        client_stored = {k: v for k, v in stored_student.items() if k not in ("source_file", "source_page", "source_row", "extraction_method", "needs_review", "review_reason")}
        payload["stored_record"] = client_stored
        payload["profile_data"] = client_stored
        if not outcome.has_data:
            payload["connected"] = True
            payload["status"] = "verified_database"
            payload["message"] = "Student master record loaded from official MLRITM database."
            year_str = stored_student.get("year_of_study") or stored_student.get("year") or "—"
            sem_disp = stored_student.get("semester_display") or stored_student.get("current_semester") or "—"
            payload["identity"] = {
                "name": stored_student.get("student_name") or stored_student.get("name"),
                "roll_number": roll,
                "department": stored_student.get("branch"),
                "course": "B.Tech",
                "year": year_str,
                "section": stored_student.get("section"),
                "academic_year": stored_student.get("academic_year"),
            }
            payload["current_semester"] = sem_disp
            payload["year"] = year_str
            payload["year_of_study"] = year_str
        else:
            year_str = stored_student.get("year_of_study") or stored_student.get("year") or "—"
            if not payload.get("year"):
                payload["year"] = year_str
            if not payload.get("year_of_study"):
                payload["year_of_study"] = year_str
    else:
        payload["stored_record"] = None
        payload["profile_data"] = None

    return payload


@router.post("/sync")
async def sync_now(
    student: Optional[StudentContext] = Depends(get_student_context),
    db: AsyncSession = Depends(get_db),
):
    """Forces a fresh read of the signed-in student's authorized record from Anvaya."""
    authorized = _authorized(student)
    outcome = await sync_service.get_profile(db, authorized, force=True)
    return _profile_payload(outcome)


@router.get("/record/{kind}")
async def student_record(
    kind: RecordKind,
    student: Optional[StudentContext] = Depends(get_student_context),
    db: AsyncSession = Depends(get_db),
):
    """
    One category of the signed-in student's record.

    Returns the same minimal selection the chat pipeline would use, plus the Markdown the
    interface renders, so a quick-action button and a typed question give identical answers.
    """
    authorized = _authorized(student)
    intent, question = RECORD_INTENTS[kind]

    if getattr(settings, "STUDENT_DATA_PROVIDER", "none") != "sample":
        from backend.app.services.live_data import live_data_service
        roll = authorized.roll_number or authorized.student_key
        live_res = await live_data_service.get_feature_data(roll, kind.value)
        await audit.record(
            db, "RECORD_ACCESS", student=authorized, intent=intent.value, data_category=kind.value,
            detail=f"mode={live_res.mode}, status={live_res.status}",
        )
        return {
            "kind": kind.value,
            "current_semester": None,
            "data": live_res.raw_data or {},
            "formatted_markdown": live_res.answer,
            "last_synced_at": None,
            "source": live_res.source,
            "is_stale": live_res.status == "unavailable",
            "deep_link_url": live_res.deep_link_url,
            "mode": live_res.mode,
            "notes": [],
        }

    try:
        context = await context_engine.build(db, authorized, intent, question)
    except StudentAccessDenied as denied:
        code, message = DENIED_STATUS[denied.reason]
        raise HTTPException(status_code=code, detail=message)

    if not context.available:
        await audit.record(
            db, "RECORD_ACCESS", student=authorized, intent=intent.value,
            data_category=kind.value, status="UNAVAILABLE", detail=context.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=context.message or "That record is not available from Anvaya right now.",
        )

    bundle = context.bundle
    await audit.record(
        db, "RECORD_ACCESS", student=authorized, intent=intent.value, data_category=kind.value,
    )

    return {
        "kind": kind.value,
        "current_semester": bundle.current_semester,
        "data": bundle.sections,
        "formatted_markdown": format_context_bundle(bundle),
        "last_synced_at": bundle.last_synced_at,
        "source": bundle.source,
        "is_stale": bundle.is_stale,
        "notes": bundle.notes,
    }


@router.delete("/data")
async def erase_student_data(
    student: Optional[StudentContext] = Depends(get_student_context),
    db: AsyncSession = Depends(get_db),
):
    """Deletes this student's stored snapshot (DPDP Act 2023 erasure / consent withdrawal)."""
    authorized = _authorized(student)
    await student_profile_store.purge(db, authorized)
    await audit.record(db, "PROFILE_ERASE", student=authorized, data_category="profile")
    return {"message": "Your stored academic snapshot has been deleted from this assistant."}
