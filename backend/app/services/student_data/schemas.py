"""
Pydantic Schemas for Student Academic Records.
Typed data models for Attendance, Examination Results, Timetables, and Holidays.

Every student-specific record carries `student_key`: the server-side key of the student it
belongs to. The authorization layer rejects any record whose key differs from the signed-in
student's key before it reaches the response formatter or the LLM.
"""

from typing import List, Optional
from datetime import date, datetime
from pydantic import BaseModel, Field


class SubjectAttendance(BaseModel):
    subject_code: str = Field(..., examples=["CS501PC"])
    subject_name: str = Field(..., examples=["Design and Analysis of Algorithms"])
    attended_classes: int = Field(..., ge=0)
    total_classes: int = Field(..., ge=0)
    percentage: float = Field(..., ge=0.0, le=100.0)
    is_shortage: bool = Field(default=False)
    classes_needed_for_75: int = Field(default=0)


class AttendanceRecord(BaseModel):
    student_key: str
    roll_number: Optional[str] = None
    total_attended: int
    total_conducted: int
    aggregate_percentage: float
    is_shortage: bool
    condonation_eligible: bool
    detention_risk: bool
    classes_needed_for_75: int
    max_bunks_permitted: int
    subjects: List[SubjectAttendance]
    last_synced: datetime = Field(default_factory=datetime.utcnow)
    source: str = "Anvaya ERP"


class SubjectResult(BaseModel):
    subject_code: str
    subject_name: str
    internal_marks: Optional[int] = None
    external_marks: Optional[int] = None
    total_marks: Optional[int] = None
    grade: str = Field(..., examples=["A"])
    grade_points: int = Field(..., ge=0, le=10)
    credits: float = Field(..., ge=0.0)
    status: str = Field(..., examples=["PASS"])  # PASS or FAIL


class SemesterResult(BaseModel):
    student_key: str
    roll_number: Optional[str] = None
    semester: int
    exam_month_year: str
    sgpa: float
    cgpa: float
    total_credits: float
    backlogs_count: int
    subjects: List[SubjectResult]
    last_synced: datetime = Field(default_factory=datetime.utcnow)
    source: str = "Anvaya ERP"


class PeriodSlot(BaseModel):
    period_number: int
    start_time: str
    end_time: str
    subject_name: str
    faculty_name: str
    room_number: str


class DayTimetable(BaseModel):
    day_of_week: str  # Monday, Tuesday, etc.
    periods: List[PeriodSlot]


class TimetableRecord(BaseModel):
    student_key: str
    roll_number: Optional[str] = None
    branch: str
    section: str
    weekly_schedule: List[DayTimetable]
    last_synced: datetime = Field(default_factory=datetime.utcnow)
    source: str = "Anvaya ERP"


class HolidayItem(BaseModel):
    holiday_date: date
    holiday_name: str
    holiday_type: str  # Festival, National, Semester Break
    is_restricted: bool = False


class HolidayRecord(BaseModel):
    academic_year: str
    semester: str
    holidays: List[HolidayItem]
    working_days_remaining: int
    last_synced: datetime = Field(default_factory=datetime.utcnow)
    source: str = "MLRITM Academic Calendar"


# ---------------------------------------------------------------------------
# Current-semester academic context
#
# Nothing here has a "plausible looking" default: every field the official source does not
# supply stays None / empty so the assistant can say "not available" instead of inventing a
# department, a semester or a section for a real student.
# ---------------------------------------------------------------------------


class StudentSubject(BaseModel):
    subject_code: str
    subject_name: str
    subject_type: str = "Theory"  # Theory / Laboratory / Mandatory Course
    credits: Optional[float] = None
    semester: Optional[int] = None
    faculty_name: Optional[str] = None
    syllabus_summary: Optional[str] = None


class FacultyInfo(BaseModel):
    name: str
    designation: Optional[str] = None
    department: Optional[str] = None
    subject_code: Optional[str] = None
    subject_name: Optional[str] = None
    semester: Optional[int] = None
    room_number: Optional[str] = None
    email: Optional[str] = None
    office_hours: Optional[str] = None


class SubjectInternalMark(BaseModel):
    subject_code: str
    subject_name: str
    semester: Optional[int] = None
    mid1_marks: Optional[float] = None
    mid2_marks: Optional[float] = None
    assignment_marks: Optional[float] = None
    lab_internal: Optional[float] = None
    best_mid_avg: Optional[float] = None
    max_marks: Optional[float] = None


class ExamScheduleItem(BaseModel):
    subject_code: str
    subject_name: str
    exam_type: str  # Mid-1, Mid-2, Semester End Examination
    exam_date: Optional[str] = None
    time_slot: Optional[str] = None
    room_number: Optional[str] = None
    hall_ticket_status: Optional[str] = None
    semester: Optional[int] = None


class AssignmentItem(BaseModel):
    subject_code: str
    subject_name: str
    title: str
    due_date: Optional[str] = None
    status: Optional[str] = None  # Submitted / Pending / Evaluated
    max_marks: Optional[float] = None
    semester: Optional[int] = None


class BacklogItem(BaseModel):
    subject_code: str
    subject_name: str
    semester: Optional[int] = None
    exam_month_year: Optional[str] = None
    attempts: Optional[int] = None
    credits: Optional[float] = None
    status: str = "PENDING"  # PENDING / CLEARED


class AcademicNotification(BaseModel):
    id: Optional[str] = None
    title: str
    message: str
    date: Optional[str] = None
    category: str = "General"  # Exam, Fee, Attendance, Academic, Event
    priority: str = "Normal"   # Urgent, Important, Normal


class StudentAcademicProfile(BaseModel):
    """
    The authorized snapshot of ONE student's academic context, as returned by the official
    Anvaya data interface and cached in the profile store.

    This whole object is never handed to the language model. The context engine selects the
    few fields a question actually needs (see services/context_engine.py).
    """
    student_key: str
    roll_number: Optional[str] = None
    display_name: Optional[str] = None
    anvaya_user_id: Optional[str] = None
    student_id: Optional[str] = None
    department: Optional[str] = None
    course: Optional[str] = None
    year: Optional[str] = None
    current_semester: Optional[int] = None
    section: Optional[str] = None
    academic_year: Optional[str] = None
    email: Optional[str] = None          # only when the official source authorizes it

    attendance: Optional[AttendanceRecord] = None
    current_subjects: List[StudentSubject] = Field(default_factory=list)
    faculty_list: List[FacultyInfo] = Field(default_factory=list)
    internal_marks: List[SubjectInternalMark] = Field(default_factory=list)
    upcoming_exams: List[ExamScheduleItem] = Field(default_factory=list)
    semester_results: Optional[SemesterResult] = None
    timetable: Optional[TimetableRecord] = None
    assignments: List[AssignmentItem] = Field(default_factory=list)
    backlogs: List[BacklogItem] = Field(default_factory=list)
    notifications: List[AcademicNotification] = Field(default_factory=list)

    last_synced: datetime = Field(default_factory=datetime.utcnow)
    source: str = "Anvaya ERP"
