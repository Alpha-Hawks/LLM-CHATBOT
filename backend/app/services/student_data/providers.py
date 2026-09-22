"""
Student Data Providers (the Student Data Service).

    none          UnconfiguredStudentDataProvider  - default; personal records are unavailable
    anvaya_api    AnvayaApiStudentDataProvider     - official server-to-server interface, once
                                                     MLRITM / ORGMAKER supplies its base URL,
                                                     credentials and response documentation
    sample        SampleStudentDataProvider        - clearly labelled development records,
                                                     refused outside ENVIRONMENT=development

`get_student_data_provider()` picks one from STUDENT_DATA_PROVIDER and fails closed: anything
unrecognised, unconfigured, or not permitted in this environment resolves to "none".
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from backend.app.core.config import settings
from backend.app.services.academic_calculator import calculate_attendance_metrics
from backend.app.services.identity.base import StudentContext
from backend.app.services.student_data.base import (
    StudentDataNotConfigured,
    StudentDataProvider,
    StudentDataUnavailable,
)
from backend.app.services.student_data.schemas import (
    AcademicNotification,
    AssignmentItem,
    AttendanceRecord,
    BacklogItem,
    DayTimetable,
    ExamScheduleItem,
    FacultyInfo,
    PeriodSlot,
    SemesterResult,
    StudentAcademicProfile,
    StudentSubject,
    SubjectAttendance,
    SubjectInternalMark,
    SubjectResult,
    TimetableRecord,
)

logger = logging.getLogger(__name__)

SAMPLE_SOURCE = "Sample data (development only, not from Anvaya)"


class UnconfiguredStudentDataProvider(StudentDataProvider):
    """Fail-closed default. Nothing personal can be produced, so nothing can be leaked."""

    name = "none"

    async def get_profile(self, student: StudentContext) -> StudentAcademicProfile:
        raise StudentDataNotConfigured(
            "Personal academic records are not connected yet. MLRITM / ORGMAKER has not provided an "
            "official data interface for this assistant."
        )


# =============================================================================================
# Official Anvaya interface
# =============================================================================================

class AnvayaApiStudentDataProvider(StudentDataProvider):
    """
    Read-only, server-to-server client for the official Anvaya / ORGMAKER student data API.

    Everything it needs comes from configuration supplied by MLRITM, so no endpoint, field name
    or credential is guessed here, and the provider stays disabled until `is_configured()` holds:

        ANVAYA_API_BASE_URL          https:// base of the approved API
        ANVAYA_API_STUDENT_PATH      path template containing {student_key}
        ANVAYA_API_AUTH_SCHEME       bearer | header | none
        ANVAYA_API_TOKEN             the issued service credential (never a student password)
        ANVAYA_API_TOKEN_HEADER      header name when the scheme is "header"

    The student key in the path comes from the verified Anvaya identity held in the server-side
    session - never from the browser, the question text or the intent router.

    ADAPTING THIS PROVIDER: `_map_profile()` below is the single place that turns the API's JSON
    into `StudentAcademicProfile`. Adjust its field names to the response documentation MLRITM
    provides. Anything the response does not contain must stay None / empty.
    """

    name = "anvaya_api"

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._transport = transport

    def is_configured(self) -> bool:
        base, path = settings.ANVAYA_API_BASE_URL.strip(), settings.ANVAYA_API_STUDENT_PATH.strip()
        has_identifier = any(
            k in path for k in ("{student_key}", "{roll_number}", "{student_id}", "{anvaya_user_id}")
        )
        if not base or not has_identifier:
            return False
        if not base.lower().startswith("https://") and settings.ENVIRONMENT != "development":
            logger.error("ANVAYA_API_BASE_URL must use https:// outside development.")
            return False
        scheme = settings.ANVAYA_API_AUTH_SCHEME.strip().lower()
        if scheme in ("bearer", "header") and not settings.ANVAYA_API_TOKEN:
            return False
        return scheme in ("bearer", "header", "none")

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        scheme = settings.ANVAYA_API_AUTH_SCHEME.strip().lower()
        if scheme == "bearer":
            headers["Authorization"] = f"Bearer {settings.ANVAYA_API_TOKEN}"
        elif scheme == "header":
            headers[settings.ANVAYA_API_TOKEN_HEADER or "X-API-Key"] = settings.ANVAYA_API_TOKEN
        return headers

    async def get_profile(self, student: StudentContext) -> StudentAcademicProfile:
        if not self.is_configured():
            raise StudentDataNotConfigured(
                "The official Anvaya data interface is not configured for this assistant yet."
            )

        path = settings.ANVAYA_API_STUDENT_PATH.lstrip("/").format(
            student_key=student.student_key,
            roll_number=student.roll_number or student.student_key,
            student_id=getattr(student, "student_id", student.roll_number) or student.student_key,
            anvaya_user_id=getattr(student, "anvaya_user_id", None) or f"anvaya-{student.student_key}",
        )
        url = settings.ANVAYA_API_BASE_URL.rstrip("/") + "/" + path
        try:
            async with httpx.AsyncClient(
                timeout=settings.ANVAYA_API_TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.get(url, headers=self._headers())
        except httpx.HTTPError as e:
            # The URL can contain the student key, so log the failure type only
            logger.warning(f"Anvaya data API unreachable ({type(e).__name__}).")
            raise StudentDataUnavailable("Anvaya is not responding right now, so I could not load your records.")

        if response.status_code == 404:
            raise StudentDataUnavailable("Anvaya has no academic record for your account yet.")
        if response.status_code in (401, 403):
            logger.error(f"Anvaya data API refused this service credential (HTTP {response.status_code}).")
            raise StudentDataUnavailable("This assistant is not currently authorized to read your Anvaya records.")
        if response.status_code != 200:
            logger.warning(f"Anvaya data API returned HTTP {response.status_code}.")
            raise StudentDataUnavailable("Anvaya returned an unexpected response, so I could not load your records.")

        try:
            payload = response.json()
        except ValueError:
            raise StudentDataUnavailable("Anvaya returned a response I could not read.")
        if not isinstance(payload, dict):
            raise StudentDataUnavailable("Anvaya returned a response I could not read.")

        return self._map_profile(student, payload)

    # -- The one function to adapt to the official response documentation ---------------------

    def _map_profile(self, student: StudentContext, payload: Dict[str, Any]) -> StudentAcademicProfile:
        """
        Maps the API's JSON onto the profile schema.

        `student_key` is taken from the session, never from the payload, so a mislabelled or
        tampered response cannot make one student's data look like another's. The authorization
        layer then re-checks ownership before anything reaches the student.
        """
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

        def field(*names):
            for name in names:
                if data.get(name) not in (None, ""):
                    return data[name]
            return None

        def as_int(value):
            try:
                return int(str(value).strip())
            except (TypeError, ValueError):
                return None

        profile = StudentAcademicProfile(
            student_key=student.student_key,
            anvaya_user_id=field("anvaya_user_id", "anvayaUserId") or getattr(student, "anvaya_user_id", None) or student.student_key,
            student_id=field("student_id", "studentId", "id") or getattr(student, "student_id", None) or student.roll_number or student.student_key,
            roll_number=field("roll_number", "rollNo", "admission_number") or student.roll_number,
            display_name=field("name", "student_name", "full_name") or student.display_name,
            department=field("department", "branch", "dept"),
            course=field("course", "program", "degree"),
            year=field("year", "study_year"),
            current_semester=as_int(field("current_semester", "semester", "sem")),
            section=field("section"),
            academic_year=field("academic_year", "academicYear"),
            email=field("email", "college_email"),
            last_synced=datetime.utcnow(),
            source="Anvaya ERP",
        )

        list_sections = {
            "current_subjects": (("subjects", "current_subjects"), StudentSubject),
            "faculty_list": (("faculty", "faculty_list"), FacultyInfo),
            "internal_marks": (("internal_marks", "internals"), SubjectInternalMark),
            "upcoming_exams": (("exams", "upcoming_exams", "exam_schedule"), ExamScheduleItem),
            "assignments": (("assignments",), AssignmentItem),
            "backlogs": (("backlogs",), BacklogItem),
            "notifications": (("notifications", "academic_notifications"), AcademicNotification),
        }
        for attribute, (keys, model) in list_sections.items():
            rows = next((data[k] for k in keys if isinstance(data.get(k), list)), [])
            parsed = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    parsed.append(model(**row))
                except Exception:
                    logger.warning(f"Skipped an Anvaya {attribute} row that did not match the expected shape.")
            setattr(profile, attribute, parsed)

        for attribute, keys, model in (
            ("attendance", ("attendance",), AttendanceRecord),
            ("semester_results", ("results", "semester_results"), SemesterResult),
            ("timetable", ("timetable",), TimetableRecord),
        ):
            raw = next((data[k] for k in keys if isinstance(data.get(k), dict)), None)
            if raw is None:
                continue
            try:
                setattr(profile, attribute, model(**{**raw, "student_key": student.student_key}))
            except Exception:
                logger.warning(f"Anvaya {attribute} did not match the expected shape; left unavailable.")

        if not getattr(profile, "source", None):
            profile.source = "Anvaya ERP (Live)"
        if not getattr(profile, "last_synced", None):
            profile.last_synced = datetime.utcnow()
        return profile


# =============================================================================================
# Development sample records
# =============================================================================================

def _sample_cohort(roll: str) -> Dict[str, Any]:
    """One internally consistent cohort so attendance, marks, faculty and timetable agree."""
    if "1201" in roll:
        return {
            "department": "Information Technology",
            "year": "2nd Year",
            "current_semester": 4,
            "section": "A",
            "academic_year": "2024-2025",
            # code, name, type, credits, attended, conducted, faculty, designation, room, mid1, mid2, assignment
            "subjects": [
                ("IT401PC", "Database Management Systems", "Theory", 3.0, 41, 50, "Dr. S. Sharma", "Professor", "LH-204", 24, 26, 5),
                ("IT402PC", "Operating Systems", "Theory", 3.0, 37, 50, "Prof. M. Rao", "Associate Professor", "LH-204", 20, 22, 4),
                ("IT403PC", "Artificial Intelligence", "Theory", 3.0, 91, 100, "Dr. S. Anand", "Professor", "LH-205", 25, 26, 5),
                ("IT404PC", "Web Technologies", "Theory", 3.0, 43, 50, "Mrs. P. Swathi", "Assistant Professor", "LH-204", 23, 24, 5),
                ("IT405PC", "Discrete Mathematics", "Theory", 3.0, 40, 50, "Mr. R. Kiran", "Assistant Professor", "LH-206", 21, 23, 4),
                ("IT406PC", "Java Programming Lab", "Laboratory", 1.5, 11, 12, "Dr. V. Prasad", "Professor", "Lab 2", None, None, None),
                ("IT407PC", "OS & DBMS Lab", "Laboratory", 1.5, 11, 12, "Prof. M. Rao", "Associate Professor", "Lab 3", None, None, None),
            ],
            "cgpa": 8.62,
            "notifications": [
                AcademicNotification(
                    id="NOTIF-01",
                    title="Mid-2 Examination Schedule Released",
                    message="Mid-2 examinations for B.Tech II Year II Semester commence from March 25, 2025.",
                    date="2025-03-10",
                    category="Exam",
                    priority="Important",
                ),
            ],
        }

    if "1270" in roll or "IT" in roll.upper():
        return {
            "department": "Information Technology",
            "year": "2nd Year",
            "current_semester": 4,
            "section": "B",
            "academic_year": "2024-2025",
            # code, name, type, credits, attended, conducted, faculty, designation, room, mid1, mid2, assignment
            "subjects": [
                ("IT401PC", "Database Management Systems", "Theory", 3.0, 32, 44, "Dr. S. Sharma", "Professor", "LH-204", 22, 24, 5),
                ("IT402PC", "Operating Systems", "Theory", 3.0, 33, 44, "Prof. M. Rao", "Associate Professor", "LH-204", 21, 23, 4),
                ("IT403PC", "Computer Organization & Architecture", "Theory", 3.0, 30, 42, "Mrs. K. Lavanya", "Assistant Professor", "LH-205", 19, 20, 4),
                ("IT404PC", "Object Oriented Programming through Java", "Theory", 3.0, 31, 44, "Dr. V. Prasad", "Professor", "LH-204", 23, 25, 5),
                ("IT405PC", "Discrete Mathematics", "Theory", 3.0, 29, 42, "Mr. R. Kiran", "Assistant Professor", "LH-206", 18, 19, 3),
                ("IT406PC", "Java Programming Lab", "Laboratory", 1.5, 9, 12, "Dr. V. Prasad", "Professor", "Lab 2", None, None, None),
                ("IT407PC", "OS & DBMS Lab", "Laboratory", 1.5, 9, 12, "Prof. M. Rao", "Associate Professor", "Lab 3", None, None, None),
            ],
            "cgpa": 8.28,
            "notifications": [
                AcademicNotification(
                    id="NOTIF-01",
                    title="Mid-2 Examination Schedule Released",
                    message="Mid-2 examinations for B.Tech II Year II Semester commence from March 25, 2025.",
                    date="2025-03-10",
                    category="Exam",
                    priority="Important",
                ),
                AcademicNotification(
                    id="NOTIF-02",
                    title="Attendance Shortage Advisory",
                    message="Your attendance in Discrete Mathematics is below 75%. Please attend upcoming tutorial classes.",
                    date="2025-03-08",
                    category="Attendance",
                    priority="Urgent",
                ),
            ],
        }

    if "0501" in roll or "CSE-7" in roll.upper() or "SEM7" in roll.upper():
        return {
            "department": "Computer Science & Engineering",
            "year": "4th Year",
            "current_semester": 7,
            "section": "A",
            "academic_year": "2024-2025",
            "subjects": [
                ("CS701PC", "Cloud Computing", "Theory", 3.0, 42, 46, "Dr. P. Reddy", "Professor", "LH-401", 26, 27, 5),
                ("CS702PC", "Cryptography & Network Security", "Theory", 3.0, 40, 46, "Prof. V. Rao", "Associate Professor", "LH-401", 24, 25, 5),
                ("CS703PC", "Deep Learning", "Theory", 3.0, 38, 44, "Dr. M. Swaroop", "Professor", "LH-402", 25, 26, 4),
                ("CS704PE", "Natural Language Processing", "Theory", 3.0, 39, 44, "Mrs. T. Anitha", "Assistant Professor", "LH-403", 23, 25, 5),
                ("CS705OE", "Total Quality Management", "Theory", 3.0, 41, 46, "Prof. H. Srinivas", "Associate Professor", "LH-401", 24, 26, 5),
                ("CS706PC", "Cloud & Security Lab", "Laboratory", 1.5, 14, 15, "Dr. P. Reddy", "Professor", "Lab 5", None, None, None),
                ("CS707PW", "Project Stage-I", "Laboratory", 3.0, 15, 15, "Dr. M. Swaroop", "Professor", "Project Lab", None, None, None),
            ],
            "cgpa": 8.75,
            "notifications": [
                AcademicNotification(
                    id="NOTIF-10",
                    title="Campus Placement Drive Registration",
                    message="TCS Digital and Cognizant technical assessment rounds scheduled for next Tuesday in CSE Seminar Hall.",
                    date="2025-03-12",
                    category="Academic",
                    priority="Urgent",
                ),
                AcademicNotification(
                    id="NOTIF-11",
                    title="Project Stage-1 Review Submission",
                    message="Submit Stage-1 progress documentation and architecture report to your guide before March 30.",
                    date="2025-03-05",
                    category="Academic",
                    priority="Important",
                ),
            ],
        }

    if "0415" in roll or "ECE" in roll.upper() or "04" in roll:
        return {
            "department": "Electronics & Communication Engineering",
            "year": "3rd Year",
            "current_semester": 5,
            "section": "C",
            "academic_year": "2024-2025",
            "subjects": [
                ("EC501PC", "Digital Signal Processing", "Theory", 3.0, 36, 44, "Prof. K. Ramesh", "Professor", "LH-101", 22, 23, 4),
                ("EC502PC", "VLSI Design", "Theory", 3.0, 35, 44, "Dr. B. Suresh", "Professor", "LH-101", 20, 21, 4),
                ("EC503PC", "Antennas and Propagation", "Theory", 3.0, 34, 42, "Mrs. S. Padma", "Assistant Professor", "LH-102", 19, 22, 4),
                ("EC504PC", "Microprocessors & Microcontrollers", "Theory", 3.0, 37, 44, "Mr. D. Praveen", "Assistant Professor", "LH-101", 23, 24, 5),
                ("EC505PC", "DSP Lab", "Laboratory", 1.5, 12, 14, "Prof. K. Ramesh", "Professor", "ECE Lab 2", None, None, None),
                ("EC506PC", "VLSI Lab", "Laboratory", 1.5, 13, 14, "Dr. B. Suresh", "Professor", "ECE Lab 1", None, None, None),
            ],
            "cgpa": 8.15,
            "notifications": [
                AcademicNotification(
                    id="NOTIF-20",
                    title="Cadence VLSI Design Workshop",
                    message="IEEE Student Branch is hosting a 2-day workshop on Cadence Virtuoso tools this weekend.",
                    date="2025-03-11",
                    category="Event",
                    priority="Normal",
                ),
            ],
        }

    return {
        "department": "Computer Science & Engineering",
        "year": "3rd Year",
        "current_semester": 5,
        "section": "A",
        "academic_year": "2024-2025",
        "subjects": [
            ("CS501PC", "Design and Analysis of Algorithms", "Theory", 3.0, 38, 45, "Dr. A. Reddy", "Professor", "LH-301", 23, 24, 5),
            ("CS502PC", "Computer Networks", "Theory", 3.0, 35, 44, "Prof. N. Kumar", "Associate Professor", "LH-301", 21, 22, 4),
            ("CS503PC", "DevOps & Cloud Computing", "Theory", 3.0, 27, 42, "Mrs. P. Swathi", "Assistant Professor", "LH-302", 17, 18, 3),
            ("CS504PC", "Software Engineering", "Theory", 3.0, 40, 45, "Dr. G. Meena", "Professor", "LH-301", 24, 25, 5),
            ("CS505PC", "Artificial Intelligence", "Theory", 3.0, 36, 44, "Dr. S. Anand", "Professor", "LH-303", 22, 23, 4),
            ("CS506PC", "Networks Lab", "Laboratory", 1.5, 18, 20, "Prof. N. Kumar", "Associate Professor", "Lab 1", None, None, None),
            ("CS507PC", "Algorithms Lab", "Laboratory", 1.5, 19, 20, "Dr. A. Reddy", "Professor", "Lab 4", None, None, None),
        ],
        "cgpa": 8.42,
        "notifications": [
            AcademicNotification(
                id="NOTIF-30",
                title="MLRITM Technical Fest Registrations Open",
                message="Project expo and coding hackathon registrations are now open on the college portal.",
                date="2025-03-14",
                category="Event",
                priority="Normal",
            ),
        ],
    }


class SampleStudentDataProvider(StudentDataProvider):
    """
    Fixed demonstration records for local development and project demos.

    Every record is stamped with the signed-in student's own key and labelled as sample data, so
    a demo can never be mistaken for a real Anvaya record. Refused outside development.
    """

    name = "sample"

    async def get_profile(self, student: StudentContext) -> StudentAcademicProfile:
        roll = (student.roll_number or "").strip().upper()
        cohort = _sample_cohort(roll)
        semester = cohort["current_semester"]
        rows = cohort["subjects"]

        subject_attendance, subjects, faculty, internal_marks = [], [], [], []
        for code, name, kind, credits, attended, conducted, teacher, designation, room, mid1, mid2, assignment in rows:
            metrics = calculate_attendance_metrics(attended, conducted, target_pct=75.0)
            subject_attendance.append(SubjectAttendance(
                subject_code=code,
                subject_name=name,
                attended_classes=attended,
                total_classes=conducted,
                percentage=metrics["percentage"],
                is_shortage=metrics["is_shortage"],
                classes_needed_for_75=metrics["classes_needed_for_75"],
            ))
            subjects.append(StudentSubject(
                subject_code=code, subject_name=name, subject_type=kind,
                credits=credits, semester=semester, faculty_name=teacher,
            ))
            faculty.append(FacultyInfo(
                name=teacher, designation=designation, department=cohort["department"],
                subject_code=code, subject_name=name, semester=semester, room_number=room,
                office_hours="Mon-Fri, 3:30 PM - 4:30 PM",
            ))
            if mid1 is not None:
                internal_marks.append(SubjectInternalMark(
                    subject_code=code, subject_name=name, semester=semester,
                    mid1_marks=float(mid1), mid2_marks=float(mid2),
                    assignment_marks=float(assignment) if assignment is not None else None,
                    best_mid_avg=round((max(mid1, mid2) * 0.8) + (min(mid1, mid2) * 0.2), 2),
                    max_marks=30.0,
                ))
            else:
                internal_marks.append(SubjectInternalMark(
                    subject_code=code, subject_name=name, semester=semester,
                    lab_internal=26.0, max_marks=30.0,
                ))

        total_attended = sum(r[4] for r in rows)
        total_conducted = sum(r[5] for r in rows)
        aggregate = calculate_attendance_metrics(total_attended, total_conducted, target_pct=75.0)

        attendance = AttendanceRecord(
            student_key=student.student_key,
            roll_number=student.roll_number,
            total_attended=total_attended,
            total_conducted=total_conducted,
            aggregate_percentage=aggregate["percentage"],
            is_shortage=aggregate["is_shortage"],
            condonation_eligible=aggregate["condonation_eligible"],
            detention_risk=aggregate["detention_risk"],
            classes_needed_for_75=aggregate["classes_needed_for_75"],
            max_bunks_permitted=aggregate["max_bunks_permitted"],
            subjects=subject_attendance,
            last_synced=datetime.utcnow(),
            source=SAMPLE_SOURCE,
        )

        grade_scale = [("O", 10), ("A+", 9), ("A", 8), ("B+", 7)]
        results = []
        for index, row in enumerate(rows):
            code, name, _kind, credits = row[0], row[1], row[2], row[3]
            grade, points = grade_scale[index % len(grade_scale)]
            internal = 22 + (index % 4)
            external = 50 + (index % 5) * 4
            results.append(SubjectResult(
                subject_code=code, subject_name=name, internal_marks=internal, external_marks=external,
                total_marks=internal + external, grade=grade, grade_points=points,
                credits=credits, status="PASS",
            ))
        total_credits = sum(r.credits for r in results)
        sgpa = round(sum(r.grade_points * r.credits for r in results) / total_credits, 2)
        semester_results = SemesterResult(
            student_key=student.student_key,
            roll_number=student.roll_number,
            semester=max(1, semester - 1),
            exam_month_year="June 2024",
            sgpa=sgpa,
            cgpa=cohort["cgpa"],
            total_credits=total_credits,
            backlogs_count=0,
            subjects=results,
            last_synced=datetime.utcnow(),
            source=SAMPLE_SOURCE,
        )

        theory = [r for r in rows if r[2] == "Theory"]
        lab = next((r for r in rows if r[2] == "Laboratory"), None)
        starts = ["09:20 AM", "10:20 AM", "11:30 AM", "01:10 PM", "02:10 PM"]
        ends = ["10:20 AM", "11:20 AM", "12:30 PM", "02:10 PM", "03:10 PM"]
        periods = [
            PeriodSlot(period_number=index + 1, start_time=start, end_time=end,
                       subject_name=row[1], faculty_name=row[6], room_number=row[8])
            for index, (row, start, end) in enumerate(zip(theory, starts, ends))
        ]
        if lab:
            periods.append(PeriodSlot(
                period_number=len(periods) + 1, start_time="03:10 PM", end_time="05:10 PM",
                subject_name=lab[1], faculty_name=lab[6], room_number=lab[8],
            ))
        timetable = TimetableRecord(
            student_key=student.student_key,
            roll_number=student.roll_number,
            branch=cohort["department"],
            section=cohort["section"],
            weekly_schedule=[
                DayTimetable(day_of_week=day, periods=periods)
                for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
            ],
            last_synced=datetime.utcnow(),
            source=SAMPLE_SOURCE,
        )

        theory_rows = [(r[0], r[1]) for r in rows if r[2] == "Theory"]
        exams = [
            ExamScheduleItem(
                subject_code=code, subject_name=name, exam_type="Mid-2 Examination",
                exam_date=f"2025-03-{10 + index:02d}", time_slot="10:00 AM - 11:30 AM",
                room_number="Exam Hall 1", hall_ticket_status="Issued", semester=semester,
            )
            for index, (code, name) in enumerate(theory_rows)
        ]
        assignments = [
            AssignmentItem(
                subject_code=code, subject_name=name,
                title=f"{name} - Unit {2 + (index % 3)} assignment",
                due_date=f"2025-02-{12 + index:02d}",
                status="Pending" if index % 3 == 0 else "Submitted",
                max_marks=5.0, semester=semester,
            )
            for index, (code, name) in enumerate(theory_rows)
        ]

        return StudentAcademicProfile(
            student_key=student.student_key,
            roll_number=student.roll_number,
            display_name=student.display_name,
            anvaya_user_id=getattr(student, "anvaya_user_id", None) or f"anvaya-{(student.roll_number or 'student').lower()}",
            student_id=getattr(student, "student_id", None) or student.roll_number or "student",
            department=cohort["department"],
            course="B.Tech",
            year=cohort["year"],
            current_semester=semester,
            section=cohort["section"],
            academic_year=cohort["academic_year"],
            email=None,                     # not authorized in the sample set
            attendance=attendance,
            current_subjects=subjects,
            faculty_list=faculty,
            internal_marks=internal_marks,
            upcoming_exams=exams,
            semester_results=semester_results,
            timetable=timetable,
            assignments=assignments,
            backlogs=[],
            notifications=cohort.get("notifications", []),
            last_synced=datetime.utcnow(),
            source=SAMPLE_SOURCE,
        )


_unconfigured = UnconfiguredStudentDataProvider()
_sample = SampleStudentDataProvider()
_anvaya_api = AnvayaApiStudentDataProvider()


def get_student_data_provider() -> StudentDataProvider:
    """Resolves STUDENT_DATA_PROVIDER, failing closed to "none"."""
    name = settings.STUDENT_DATA_PROVIDER.strip().lower()

    if name == "anvaya_api":
        if not _anvaya_api.is_configured():
            logger.error("STUDENT_DATA_PROVIDER=anvaya_api but the official API settings are incomplete.")
            return _unconfigured
        return _anvaya_api

    if name == "sample":
        if settings.ENVIRONMENT != "development":
            logger.error("STUDENT_DATA_PROVIDER=sample is refused outside ENVIRONMENT=development.")
            return _unconfigured
        return _sample

    if name not in ("none", ""):
        logger.error(f"Unknown STUDENT_DATA_PROVIDER={name!r}; personal records stay unavailable.")
    return _unconfigured
