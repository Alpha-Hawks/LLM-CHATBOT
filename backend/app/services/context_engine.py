"""
Student Context Engine  (Anvaya Auth -> Student Data Service -> Profile Store -> **Context Engine** -> AI).

The gate between the student's stored academic snapshot and anything that generates an answer.
For every question it decides, in this order:

    1. is there an authenticated session?            (authorization.py already answered this)
    2. does this question actually need personal data?
    3. which few fields would answer it?
    4. build a bundle containing only those fields.

What leaves this module is a `StudentContextBundle`: plain, explicitly listed values. The whole
profile is never passed on, and `student_key`, session identifiers, tokens, table names and
column names never appear in it, because every field is copied by name rather than dumped.

The engine selects data. It never decides whether the student is allowed to see it - that
decision belongs to services/authorization.py and has already been made before `build()` runs.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.intent_router import IntentEnum
from backend.app.services import sync_service
from backend.app.services.identity.base import StudentContext
from backend.app.services.student_data.schemas import StudentAcademicProfile

# Intents whose answer is about the student themselves
PERSONAL_INTENTS = {
    IntentEnum.ATTENDANCE,
    IntentEnum.RESULTS,
    IntentEnum.TIMETABLE,
    IntentEnum.SUBJECTS,
    IntentEnum.FACULTY,
    IntentEnum.MARKS,
    IntentEnum.EXAMS,
    IntentEnum.ASSIGNMENTS,
    IntentEnum.PROFILE,
}

# "What should I do about ...?" - answerable only by reading the student's data together with
# the official regulation, so the response engine is told to fetch the policy as well.
ADVICE_PATTERN = re.compile(
    r"\b(what should i|what do i do|am i eligible|can i apply|will i be detained|am i detained|"
    r"how do i (fix|improve|recover)|is it a problem|what happens if|do i qualify|condonation|"
    r"am i safe|how bad|what are my options)\b"
)

# Personal phrasing on an otherwise general question ("is the fee waived for me?")
POSSESSIVE_PATTERN = re.compile(r"\b(my|mine|i am|i'm|am i|do i|did i|have i|i have|for me)\b")

LOW_ATTENDANCE_PATTERN = re.compile(
    r"\b(low|lowest|short|shortage|below|under|poor|bad|weak|risk|risky|danger|detain|detained|"
    r"failing|worst|need(s)? attention|lagging)\b"
)

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# Course nicknames students actually type. Acronyms are also derived from each subject name, so
# this table only needs the ones initials do not produce.
SUBJECT_ALIASES = {
    "dbms": ["database"],
    "os": ["operating system"],
    "coa": ["computer organization"],
    "co": ["computer organization"],
    "oops": ["object oriented"],
    "oop": ["object oriented"],
    "java": ["java"],
    "dm": ["discrete mathematics"],
    "maths": ["mathematics"],
    "ai": ["artificial intelligence"],
    "ml": ["machine learning"],
    "cn": ["computer networks"],
    "networks": ["computer networks"],
    "se": ["software engineering"],
    "daa": ["design and analysis of algorithms", "algorithms"],
    "algo": ["algorithms"],
    "devops": ["devops"],
    "cloud": ["cloud computing"],
    "ds": ["data structures"],
    "python": ["python"],
    "dld": ["digital logic"],
}


class StudentContextBundle(BaseModel):
    """
    The minimum authorized context for one question.

    `sections` holds only the categories the question needs. Everything in it has been copied
    field by field from the student's own authorized snapshot.
    """

    identity: Dict[str, Any] = Field(default_factory=dict)
    current_semester: Optional[int] = None
    sections: Dict[str, Any] = Field(default_factory=dict)
    last_synced_at: Optional[datetime] = None
    source: Optional[str] = None
    is_stale: bool = False
    needs_policy: bool = False
    notes: List[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.sections

    def to_prompt_block(self) -> str:
        """
        Renders the bundle for a language model.

        Presented as verified data the backend already authorized, and deliberately without any
        identifier the model could use to ask for more: there is no key, no session, no endpoint
        and no table name to reference.
        """
        lines: List[str] = []
        if self.identity:
            lines.append("Student: " + ", ".join(f"{k}: {v}" for k, v in self.identity.items() if v is not None))
        if self.current_semester:
            lines.append(f"Current semester: {self.current_semester}")

        for label, value in self.sections.items():
            heading = label.replace("_", " ").capitalize()
            if isinstance(value, list):
                lines.append(f"{heading}:")
                lines.extend(
                    "  - " + ", ".join(f"{k}: {v}" for k, v in row.items() if v is not None)
                    for row in value if isinstance(row, dict)
                )
            elif isinstance(value, dict):
                lines.append(f"{heading}: " + ", ".join(f"{k}: {v}" for k, v in value.items() if v is not None))
            else:
                lines.append(f"{heading}: {value}")

        if self.last_synced_at:
            stamp = self.last_synced_at.strftime("%d %b %Y, %I:%M %p UTC")
            lines.append(f"Last synchronized with {self.source or 'Anvaya'}: {stamp}")
        if self.is_stale:
            lines.append("NOTE: Anvaya could not be reached; these values are the last ones retrieved.")
        return "\n".join(lines)


class ContextResult(BaseModel):
    """Either a bundle to answer with, or the reason there is none."""

    bundle: Optional[StudentContextBundle] = None
    reason: Optional[str] = None        # not_configured | unavailable | no_data
    message: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.bundle is not None


# =============================================================================================
# Question analysis
# =============================================================================================

def requires_personal_data(intent: IntentEnum, question: str) -> bool:
    """Whether answering this question means reading the student's own records."""
    if intent in PERSONAL_INTENTS:
        return True
    # Personal fee inquiry (e.g. "what is my fee status?", "my fee dues", "fee balance for me")
    if re.search(r"\b(my fees?|fee status|fee due|fee payment|fee dues?|fee balance|paid fees?|pending fee)\b", question, re.IGNORECASE):
        return True
    # A general-knowledge question asked personally ("am I eligible for condonation?") benefits
    # from the student's own figures alongside the regulation.
    return intent == IntentEnum.FAQ_RAG and bool(
        POSSESSIVE_PATTERN.search(question.lower()) and ADVICE_PATTERN.search(question.lower())
    )


def needs_official_policy(intent: IntentEnum, question: str) -> bool:
    """Whether the answer should also cite the college regulation, not just the student's numbers."""
    return bool(ADVICE_PATTERN.search(question.lower())) or intent == IntentEnum.FAQ_RAG


def _acronym(name: str) -> str:
    return "".join(word[0] for word in re.findall(r"[A-Za-z]+", name) if len(word) > 2).lower()


def match_subjects(question: str, subjects: List[Any]) -> List[Any]:
    """
    The subjects a question names, by code, full name, initials or common nickname.

    Returns [] when the question names none, which the callers read as "all of them".
    """
    text = f" {question.lower()} "
    tokens = set(re.findall(r"[a-z0-9]+", text))
    matched = []

    for subject in subjects:
        code = (getattr(subject, "subject_code", "") or "").lower()
        name = (getattr(subject, "subject_name", "") or "").lower()
        if not name and not code:
            continue

        hit = bool(code and code in text)
        hit = hit or bool(name and name in text)
        hit = hit or bool(_acronym(name) and _acronym(name) in tokens)
        if not hit:
            for alias, needles in SUBJECT_ALIASES.items():
                if alias in tokens and any(needle in name for needle in needles):
                    hit = True
                    break
        if hit:
            matched.append(subject)

    return matched


def match_day(question: str) -> Optional[str]:
    """The weekday a timetable question is about, including 'today' and 'tomorrow'."""
    text = question.lower()
    for day in DAYS:
        if day in text:
            return day.capitalize()
    if "today" in text:
        return datetime.now().strftime("%A")
    if "tomorrow" in text:
        from datetime import timedelta
        return (datetime.now() + timedelta(days=1)).strftime("%A")
    return None


# =============================================================================================
# Field selection - every value below is copied by name, never dumped wholesale
# =============================================================================================

def _for_current_semester(rows: List[Any], semester: Optional[int]) -> List[Any]:
    """This semester's rows. Rows with no semester stay in; rows from other semesters drop out."""
    if semester is None:
        return rows
    scoped = [r for r in rows if getattr(r, "semester", None) in (None, semester)]
    return scoped or rows


def _attendance_section(profile: StudentAcademicProfile, question: str) -> Optional[Dict[str, Any]]:
    record = profile.attendance
    if record is None:
        return None

    subjects = record.subjects
    note = None
    if re.search(r"\b(lowest|worst|minimum)\b", question.lower()) and subjects:
        lowest_subject = min(subjects, key=lambda s: s.percentage)
        pct_display = int(lowest_subject.percentage) if lowest_subject.percentage.is_integer() else lowest_subject.percentage
        subjects = [lowest_subject]
        note = f"Your lowest attendance is in {lowest_subject.subject_name} at {pct_display}%."
    elif LOW_ATTENDANCE_PATTERN.search(question.lower()):
        low = [s for s in subjects if s.is_shortage or s.percentage < 75.0]
        subjects, note = low, ("no subject is below 75%" if not low else None)
    else:
        named = match_subjects(question, subjects)
        if named:
            subjects = named

    section = {
        "overall_percentage": record.aggregate_percentage,
        "classes_attended": record.total_attended,
        "classes_conducted": record.total_conducted,
        "below_required_75": record.is_shortage,
        "condonation_range_65_to_75": record.condonation_eligible,
        "detention_risk_below_65": record.detention_risk,
        "classes_needed_to_reach_75": record.classes_needed_for_75,
        "classes_that_can_be_missed": record.max_bunks_permitted,
        "subjects": [
            {
                "subject_code": s.subject_code,
                "subject_name": s.subject_name,
                "attended": s.attended_classes,
                "conducted": s.total_classes,
                "percentage": s.percentage,
                "below_75": s.is_shortage,
                "classes_needed_to_reach_75": s.classes_needed_for_75,
            }
            for s in subjects
        ],
    }
    if note:
        section["note"] = note
    return section


def _subjects_section(profile: StudentAcademicProfile, question: str, semester: Optional[int]):
    rows = _for_current_semester(profile.current_subjects, semester)
    named = match_subjects(question, rows)
    return [
        {
            "subject_code": s.subject_code,
            "subject_name": s.subject_name,
            "type": s.subject_type,
            "credits": s.credits,
            "faculty": s.faculty_name,
        }
        for s in (named or rows)
    ] or None


def _faculty_section(profile: StudentAcademicProfile, question: str, semester: Optional[int]):
    rows = _for_current_semester(profile.faculty_list, semester)
    named = match_subjects(question, rows)
    return [
        {
            "faculty_name": f.name,
            "designation": f.designation,
            "department": f.department,
            "subject_code": f.subject_code,
            "subject_name": f.subject_name,
            "room": f.room_number,
            "office_hours": f.office_hours,
            "email": f.email,
        }
        for f in (named or rows)
    ] or None


def _marks_section(profile: StudentAcademicProfile, question: str, semester: Optional[int]):
    rows = _for_current_semester(profile.internal_marks, semester)
    named = match_subjects(question, rows)
    return [
        {
            "subject_code": m.subject_code,
            "subject_name": m.subject_name,
            "mid_1": m.mid1_marks,
            "mid_2": m.mid2_marks,
            "assignment": m.assignment_marks,
            "lab_internal": m.lab_internal,
            "best_mid_average": m.best_mid_avg,
            "out_of": m.max_marks,
        }
        for m in (named or rows)
    ] or None


def _exams_section(profile: StudentAcademicProfile, question: str, semester: Optional[int]):
    rows = _for_current_semester(profile.upcoming_exams, semester)
    named = match_subjects(question, rows)
    return [
        {
            "subject_code": e.subject_code,
            "subject_name": e.subject_name,
            "exam": e.exam_type,
            "date": e.exam_date,
            "time": e.time_slot,
            "room": e.room_number,
            "hall_ticket": e.hall_ticket_status,
        }
        for e in (named or rows)
    ] or None


def _assignments_section(profile: StudentAcademicProfile, question: str, semester: Optional[int]):
    rows = _for_current_semester(profile.assignments, semester)
    named = match_subjects(question, rows)
    if "pending" in question.lower() or "due" in question.lower():
        pending = [a for a in (named or rows) if (a.status or "").lower() == "pending"]
        rows, named = (pending or rows), (pending or named)
    return [
        {
            "subject_code": a.subject_code,
            "subject_name": a.subject_name,
            "title": a.title,
            "due_date": a.due_date,
            "status": a.status,
            "out_of": a.max_marks,
        }
        for a in (named or rows)
    ] or None


def _results_section(profile: StudentAcademicProfile):
    record = profile.semester_results
    if record is None:
        return None
    return {
        "semester": record.semester,
        "examination": record.exam_month_year,
        "sgpa": record.sgpa,
        "cgpa": record.cgpa,
        "credits_earned": record.total_credits,
        "backlogs": record.backlogs_count,
        "subjects": [
            {
                "subject_code": s.subject_code,
                "subject_name": s.subject_name,
                "grade": s.grade,
                "grade_points": s.grade_points,
                "credits": s.credits,
                "result": s.status,
            }
            for s in record.subjects
        ],
    }


def _backlogs_section(profile: StudentAcademicProfile):
    if not profile.backlogs:
        return None
    return [
        {
            "subject_code": b.subject_code,
            "subject_name": b.subject_name,
            "semester": b.semester,
            "examination": b.exam_month_year,
            "attempts": b.attempts,
            "status": b.status,
        }
        for b in profile.backlogs
    ]


def _timetable_section(profile: StudentAcademicProfile, question: str):
    record = profile.timetable
    if record is None:
        return None

    day = match_day(question)
    days = [d for d in record.weekly_schedule if d.day_of_week.lower() == day.lower()] if day else record.weekly_schedule
    if day and not days:
        return {"day": day, "periods": [], "note": f"no classes are scheduled on {day}"}

    return {
        "branch": record.branch,
        "section": record.section,
        "days": [
            {
                "day": d.day_of_week,
                "periods": [
                    {
                        "period": p.period_number,
                        "time": f"{p.start_time} - {p.end_time}" if p.end_time else p.start_time,
                        "subject": p.subject_name,
                        "faculty": p.faculty_name,
                        "room": p.room_number,
                    }
                    for p in d.periods
                ],
            }
            for d in days
        ],
    }


def _notifications_section(profile: StudentAcademicProfile):
    if not getattr(profile, "notifications", None):
        return None
    return [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "date": n.date,
            "category": n.category,
            "priority": n.priority,
        }
        for n in profile.notifications
    ]


# Which sections each intent is allowed to read. Anything not listed is not selected, so a
# question about the timetable never carries the student's marks into a prompt.
def _select_sections(intent: IntentEnum, profile: StudentAcademicProfile, question: str,
                     semester: Optional[int]) -> Dict[str, Any]:
    sections: Dict[str, Any] = {}

    def put(label, value):
        if value:
            sections[label] = value

    if intent in (IntentEnum.ATTENDANCE, IntentEnum.FAQ_RAG):
        put("attendance", _attendance_section(profile, question))
    if intent == IntentEnum.SUBJECTS:
        put("current_semester_subjects", _subjects_section(profile, question, semester))
    if intent == IntentEnum.FACULTY:
        put("faculty", _faculty_section(profile, question, semester))
    if intent == IntentEnum.MARKS:
        put("internal_marks", _marks_section(profile, question, semester))
    if intent == IntentEnum.EXAMS:
        put("examination_schedule", _exams_section(profile, question, semester))
    if intent == IntentEnum.ASSIGNMENTS:
        put("assignments", _assignments_section(profile, question, semester))
    if intent == IntentEnum.RESULTS:
        put("latest_results", _results_section(profile))
        put("backlogs", _backlogs_section(profile))
    if intent == IntentEnum.TIMETABLE:
        put("timetable", _timetable_section(profile, question))
    if any(k in question.lower() for k in ("notification", "notice", "circular", "announcement", "notices")):
        put("academic_notifications", _notifications_section(profile))
    if intent == IntentEnum.PROFILE:
        put("subject_count", len(profile.current_subjects) or None)
        put("attendance_summary", {
            "overall_percentage": profile.attendance.aggregate_percentage,
            "below_required_75": profile.attendance.is_shortage,
        } if profile.attendance else None)
        put("latest_sgpa", profile.semester_results.sgpa if profile.semester_results else None)
        put("latest_cgpa", profile.semester_results.cgpa if profile.semester_results else None)
        put("pending_backlogs", len(profile.backlogs) or None)

    return sections


def identity_summary(profile: StudentAcademicProfile) -> Dict[str, Any]:
    """The few identity fields used to address the student. No key, no session, no token."""
    return {
        "name": profile.display_name,
        "roll_number": profile.roll_number,
        "department": profile.department,
        "course": profile.course,
        "year": profile.year,
        "section": profile.section,
        "academic_year": profile.academic_year,
    }


# =============================================================================================
# Entry point
# =============================================================================================

async def build(db: AsyncSession, student: StudentContext, intent: IntentEnum, question: str) -> ContextResult:
    """
    The authorized, minimal context for this question.

    Assumes authorization has already passed. Returns a reason instead of a bundle when Anvaya
    has nothing to give, so the caller can say so rather than answer from nothing.
    """
    is_dynamic = intent in (IntentEnum.ATTENDANCE, IntentEnum.MARKS) or any(
        k in question.lower() for k in ("attendance", "mark", "marks", "internal", "lowest", "bunk")
    )
    outcome = await sync_service.get_profile(db, student, prefer_fresh=is_dynamic)
    if not outcome.has_data:
        return ContextResult(
            reason="not_configured" if outcome.status == "not_configured" else "unavailable",
            message=outcome.message,
        )

    profile = outcome.profile
    semester = profile.current_semester
    sections = _select_sections(intent, profile, question, semester)

    notes = []
    if outcome.is_stale:
        notes.append("Anvaya could not be reached, so these are the last values retrieved.")
    if outcome.semester_changed:
        notes.append(f"Your current semester has been updated to Semester {semester}.")
    if semester is None:
        notes.append("Anvaya has not supplied your current semester.")

    bundle = StudentContextBundle(
        identity=identity_summary(profile),
        current_semester=semester,
        sections=sections,
        last_synced_at=outcome.last_synced_at,
        source=outcome.source,
        is_stale=outcome.is_stale,
        needs_policy=needs_official_policy(intent, question),
        notes=notes,
    )

    if bundle.is_empty():
        return ContextResult(
            bundle=bundle,
            reason="no_data",
            message="That part of your academic record is not available in Anvaya right now.",
        )
    return ContextResult(bundle=bundle)
