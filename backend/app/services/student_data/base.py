"""
Student Data Provider Contract  (Anvaya Auth -> **Student Data Service** -> Profile Store -> Context Engine -> AI).

Implement this once MLRITM / ORGMAKER documents an official, authorized data interface (for
example a read-only server-to-server API with client credentials). Rules for implementations:

- Load records ONLY for `student.student_key`, which the backend set from the verified Anvaya
  identity. A provider must never accept a roll number, key or filter from the browser.
- Stamp `student_key` on every returned record; the authorization layer rejects mismatches.
- Never ask for or use the student's Anvaya password, and never scrape Anvaya's HTML pages.
- Leave a field None when the source does not supply it. Never substitute a plausible value:
  the assistant says "not available" rather than inventing a student's semester or department.

Only `get_profile()` is required. Every other accessor defaults to slicing that snapshot, so a
provider that can fetch a student's record in one authorized call works immediately; providers
with cheaper per-category endpoints may override the accessors.

Register the implementation in student_data/providers.py.
"""

from abc import ABC, abstractmethod
from typing import List

from backend.app.services.identity.base import StudentContext
from backend.app.services.student_data.schemas import (
    AssignmentItem,
    AttendanceRecord,
    BacklogItem,
    ExamScheduleItem,
    FacultyInfo,
    SemesterResult,
    StudentAcademicProfile,
    StudentSubject,
    SubjectInternalMark,
    TimetableRecord,
)


class StudentDataNotConfigured(Exception):
    """No official student data interface has been connected yet."""


class StudentDataUnavailable(Exception):
    """
    The interface is connected but this particular record could not be retrieved
    (Anvaya unreachable, or the student's record has no such section yet).

    The assistant reports this honestly; it never fills the gap with invented data.
    """


class StudentDataProvider(ABC):
    name: str = "none"

    @abstractmethod
    async def get_profile(self, student: StudentContext) -> StudentAcademicProfile:
        """The authorized academic snapshot for the signed-in student only."""

    # -- Category accessors: default to slicing the authorized snapshot ---------------------

    async def _section(self, student: StudentContext, attribute: str, label: str):
        value = getattr(await self.get_profile(student), attribute)
        if value is None or (isinstance(value, list) and not value):
            raise StudentDataUnavailable(f"Your {label} is not available in your Anvaya record right now.")
        return value

    async def get_attendance(self, student: StudentContext) -> AttendanceRecord:
        return await self._section(student, "attendance", "attendance")

    async def get_results(self, student: StudentContext) -> SemesterResult:
        return await self._section(student, "semester_results", "examination results")

    async def get_timetable(self, student: StudentContext) -> TimetableRecord:
        return await self._section(student, "timetable", "class timetable")

    async def get_subjects(self, student: StudentContext) -> List[StudentSubject]:
        return await self._section(student, "current_subjects", "subject list")

    async def get_faculty(self, student: StudentContext) -> List[FacultyInfo]:
        return await self._section(student, "faculty_list", "faculty list")

    async def get_internal_marks(self, student: StudentContext) -> List[SubjectInternalMark]:
        return await self._section(student, "internal_marks", "internal marks")

    async def get_exams(self, student: StudentContext) -> List[ExamScheduleItem]:
        return await self._section(student, "upcoming_exams", "examination schedule")

    async def get_assignments(self, student: StudentContext) -> List[AssignmentItem]:
        return await self._section(student, "assignments", "assignment list")

    async def get_backlogs(self, student: StudentContext) -> List[BacklogItem]:
        return await self._section(student, "backlogs", "backlog list")
