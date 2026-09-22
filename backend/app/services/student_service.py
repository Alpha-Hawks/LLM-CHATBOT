"""
Official Student Master Lookup Service.
Provides secure server-side lookup for MLRITM student master records.
"""

import logging
from typing import Any, Dict, Optional, Union
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Student
from backend.app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

SEMESTER_TO_YEAR = {
    "BT2601": "1st Year",
    "BT2503": "2nd Year",
    "BT2405": "3rd Year",
    "BT2307": "4th Year",
}

SEMESTER_TO_DISPLAY = {
    "BT2601": "Semester 1 (BT2601)",
    "BT2503": "Semester 3 (BT2503)",
    "BT2405": "Semester 5 (BT2405)",
    "BT2307": "Semester 7 (BT2307)",
}


def student_to_dict(s: Student) -> Dict[str, Any]:
    """Serializes a Student ORM model to a clean dictionary."""
    sem = (s.current_semester or "").strip().upper()
    yr = SEMESTER_TO_YEAR.get(sem) or (s.year if s.year and "year" in str(s.year).lower() else None) or SEMESTER_TO_YEAR.get(sem, "Not Available")
    sem_disp = SEMESTER_TO_DISPLAY.get(sem) or s.current_semester or "Not Available"
    return {
        "roll_number": s.roll_number,
        "student_name": s.student_name,
        "name": s.student_name,
        "batch": s.batch,
        "section": s.section,
        "entry_type": s.entry_type,
        "academic_year": s.academic_year,
        "gender": s.gender,
        "date_of_birth": s.date_of_birth,
        "student_mobile": s.student_mobile,
        "student_email": s.student_email,
        "email": s.student_email,
        "father_name": s.father_name,
        "father_mobile": s.father_mobile,
        "mother_name": s.mother_name,
        "mother_mobile": s.mother_mobile,
        "branch": s.branch,
        "admission_year": s.admission_year,
        "year": yr,
        "year_of_study": yr,
        "scholarship_type": s.scholarship_type,
        "parent_income": s.parent_income,
        "parent_profession": s.parent_profession,
        "admission_category": s.admission_category,
        "caste_name": s.caste_name,
        "current_semester": s.current_semester,
        "semester_display": sem_disp,
        "source_file": s.source_file,
        "source_page": s.source_page,
        "source_row": s.source_row,
        "extraction_method": s.extraction_method,
        "needs_review": bool(s.needs_review),
        "review_reason": s.review_reason,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


async def getStudentByRollNumber(
    rollNo: str,
    session: Optional[AsyncSession] = None
) -> Union[Dict[str, Any], str]:
    """
    Look up a student record by roll number (uppercased and trimmed).
    
    Returns the student record dict if found, or 'not found' if absent.
    """
    if not rollNo:
        return "not found"

    clean_roll = rollNo.strip().upper()
    
    # If a session was passed, use it; otherwise manage our own context
    if session is not None:
        stmt = select(Student).where(Student.roll_number == clean_roll)
        result = await session.execute(stmt)
        student = result.scalar_one_or_none()
        if student is None:
            return "not found"
        return student_to_dict(student)
    else:
        async with AsyncSessionLocal() as db_session:
            stmt = select(Student).where(Student.roll_number == clean_roll)
            result = await db_session.execute(stmt)
            student = result.scalar_one_or_none()
            if student is None:
                return "not found"
            return student_to_dict(student)


def getStudentByRollNumberSync(rollNo: str, db_path: str = "academic_chatbot.db") -> Union[Dict[str, Any], str]:
    """
    Synchronous fallback lookup for standalone scripts and non-async environments.
    """
    import sqlite3
    if not rollNo:
        return "not found"

    clean_roll = rollNo.strip().upper()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM students WHERE roll_number = ?", (clean_roll,))
    row = cur.fetchone()
    conn.close()

    if row is None:
        return "not found"
    d = dict(row)
    sem = (d.get("current_semester") or "").strip().upper()
    yr = SEMESTER_TO_YEAR.get(sem) or (d.get("year") if d.get("year") and "year" in str(d.get("year")).lower() else None) or SEMESTER_TO_YEAR.get(sem, "Not Available")
    d["year"] = yr
    d["year_of_study"] = yr
    d["semester_display"] = SEMESTER_TO_DISPLAY.get(sem) or d.get("current_semester") or "Not Available"
    return d


async def getAuthorizedStudentMaster(
    student: Any,
    session: Optional[AsyncSession] = None
) -> Union[Dict[str, Any], str]:
    """
    Authorized accessor: ensures the signed-in student can read ONLY their own master record.
    Rejects any request if roll_number does not match the authenticated session.
    """
    if not student or not getattr(student, "roll_number", None):
        return "not found"
    return await getStudentByRollNumber(student.roll_number, session=session)
