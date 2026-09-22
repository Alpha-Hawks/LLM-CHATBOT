"""
Deterministic Response Formatter.
Renders typed JSON responses into clean, formatted Markdown using deterministic templates.
Guarantees:
- Numerical facts and percentages are NEVER hallucinated by LLM.
- Clear alert boxes for attendance shortage or detention risk.
- Mandatory source attribution without unwanted dates/timestamps.
"""

from datetime import datetime
from backend.app.services.student_data.schemas import (
    AttendanceRecord,
    SemesterResult,
    TimetableRecord,
    HolidayRecord,
)


def format_attendance_response(record: AttendanceRecord) -> str:
    """Formats attendance record with alerts and subject breakdown."""
    lines = []
    lines.append(f"### 📊 Attendance Report for {record.roll_number or 'you'}")
    lines.append(
        f"- **Overall Attendance:** `{record.aggregate_percentage:.1f}%` "
        f"({record.total_attended} / {record.total_conducted} classes)"
    )

    if record.is_shortage:
        if record.detention_risk:
            lines.append(
                f"\n> ⚠️ **CRITICAL ATTENDANCE WARNING:** Your attendance is below 65% "
                f"(`{record.aggregate_percentage:.1f}%`). Under MLRITM regulations you are at "
                f"**high risk of detention** and ineligible even for medical condonation."
            )
        elif record.condonation_eligible:
            lines.append(
                f"\n> ⚠️ **ATTENDANCE SHORTAGE:** Your attendance is between 65% and 75% "
                f"(`{record.aggregate_percentage:.1f}%`). You may apply for **condonation on medical "
                f"grounds** through the Academic Committee."
            )
        lines.append(
            f"\n🎯 **Recovery Target:** attend the next **{record.classes_needed_for_75} "
            f"consecutive classes** without missing any to return to **75%**."
        )
    else:
        lines.append(
            f"\n> ✅ **Status:** Your attendance is in the **safe zone** (75% or above). You can miss "
            f"up to **{record.max_bunks_permitted} classes** while staying above 75%."
        )

    if record.subjects:
        lines.append("\n#### 📚 Subject-wise Breakdown:")
        lines.append("| Subject Code | Subject Name | Attended | Conducted | Percentage | Status |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for s in record.subjects:
            badge = "🔴 Shortage" if s.is_shortage else "🟢 Safe"
            lines.append(
                f"| `{s.subject_code}` | {s.subject_name} | {s.attended_classes} | "
                f"{s.total_classes} | **{s.percentage:.1f}%** | {badge} |"
            )

    lines.append(f"\n*Source: {record.source}*")
    return "\n".join(lines)


def format_results_response(record: SemesterResult) -> str:
    """Formats semester examination results."""
    lines = []
    lines.append(f"### 🎓 Semester Examination Results (Semester {record.semester})")
    lines.append(f"- **Examination:** {record.exam_month_year}")
    lines.append(f"- **SGPA:** `{record.sgpa:.2f}` | **Cumulative CGPA:** `{record.cgpa:.2f}`")
    lines.append(f"- **Total Credits Earned:** `{record.total_credits}`")

    if record.backlogs_count > 0:
        lines.append(f"\n> ⚠️ **Backlog Notice:** you have **{record.backlogs_count}** uncleared course(s).")
    else:
        lines.append("\n> 🎉 **Status:** all subjects cleared with zero backlogs!")

    lines.append("\n#### 📋 Course Grades:")
    lines.append("| Course Code | Course Title | Grade | Grade Points | Credits | Status |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")

    for s in record.subjects:
        status_icon = "✅" if s.status == "PASS" else "❌"
        lines.append(f"| `{s.subject_code}` | {s.subject_name} | **{s.grade}** | {s.grade_points} | {s.credits} | {status_icon} {s.status} |")

    lines.append(f"\n*Source: {record.source}*")
    return "\n".join(lines)


def format_timetable_response(record: TimetableRecord, day_filter: str = None) -> str:
    """Formats weekly or daily class schedule."""
    lines = []
    lines.append(f"### 🗓️ Class Timetable (Branch: {record.branch} - Section {record.section})")

    for day in record.weekly_schedule:
        if day_filter and day_filter.lower() not in day.day_of_week.lower():
            continue

        lines.append(f"\n**{day.day_of_week}:**")
        if not day.periods:
            lines.append("- *No scheduled lectures.*")
            continue

        lines.append("| Period | Time | Subject | Faculty | Room |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for p in day.periods:
            time_slot = f"{p.start_time} - {p.end_time}" if p.end_time else p.start_time
            lines.append(f"| {p.period_number} | {time_slot} | {p.subject_name} | {p.faculty_name} | `{p.room_number}` |")

    lines.append(f"\n*Source: {record.source}*")
    return "\n".join(lines)


def format_holiday_response(record: HolidayRecord) -> str:
    """Formats upcoming holidays and academic calendar details."""
    lines = []
    lines.append(f"### 🌴 MLRITM Academic Calendar & Holidays ({record.academic_year})")
    lines.append(f"- **Working Days Remaining in Semester:** `{record.working_days_remaining} days`\n")

    lines.append("| Date | Holiday / Event | Category |")
    lines.append("| :--- | :--- | :--- |")

    for h in record.holidays:
        lines.append(f"| {h.holiday_date.strftime('%d %B %Y')} | **{h.holiday_name}** | {h.holiday_type} |")

    lines.append(f"\n*Source: {record.source}*")
    return "\n".join(lines)


# =============================================================================================
# Context-bundle rendering
#
# The chat pipeline answers personal questions from the Student Context Engine's bundle, which
# holds only the fields the question needed. These renderers are deterministic on purpose: every
# percentage, grade and date a student reads is printed straight from the authorized record, so
# no number in an answer can be a language model's invention.
# =============================================================================================

def _cell(value, dash: str = "-") -> str:
    return dash if value is None or value == "" else str(value)


def _pct_str(value) -> str:
    if value is None:
        return "-"
    try:
        f = float(value)
        if f.is_integer():
            return f"{int(f)}%"
        return f"{f}%"
    except (ValueError, TypeError):
        return f"{value}%"


def _attendance_block(section: dict, roll_number) -> str:
    lines = [f"### 📊 Attendance Report for {roll_number or 'you'}"]
    lines.append(
        f"- **Overall Attendance:** `{_pct_str(section['overall_percentage'])}` "
        f"({section['classes_attended']} / {section['classes_conducted']} classes)"
    )

    if section.get("below_required_75"):
        if section.get("detention_risk_below_65"):
            lines.append(
                f"\n> ⚠️ **CRITICAL ATTENDANCE WARNING:** Your attendance is below 65% "
                f"(`{_pct_str(section['overall_percentage'])}`). Under MLRITM regulations you are at "
                f"**high risk of detention** and ineligible even for medical condonation."
            )
        elif section.get("condonation_range_65_to_75"):
            lines.append(
                f"\n> ⚠️ **ATTENDANCE SHORTAGE:** Your attendance is between 65% and 75% "
                f"(`{_pct_str(section['overall_percentage'])}`). You may apply for **condonation on medical "
                f"grounds** through the Academic Committee."
            )
        lines.append(
            f"\n🎯 **Recovery Target:** attend the next **{section['classes_needed_to_reach_75']} "
            f"consecutive classes** without missing any to return to **75%**."
        )
    else:
        lines.append(
            f"\n> ✅ **Status:** Your attendance is in the **safe zone** (75% or above). You can miss "
            f"up to **{section['classes_that_can_be_missed']} classes** while staying above 75%."
        )

    if section.get("note"):
        if "no subject" in section["note"]:
            lines.append(f"\n> ✅ Good news - {section['note']}.")
        else:
            lines.append(f"\n> ℹ️ {section['note']}")

    if section.get("subjects"):
        lines.append("\n#### 📚 Subject-wise Breakdown:")
        lines.append("| Subject Code | Subject Name | Attended | Conducted | Percentage | Status |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for s in section["subjects"]:
            badge = "🔴 Shortage" if s["below_75"] else "🟢 Safe"
            lines.append(
                f"| `{s['subject_code']}` | {s['subject_name']} | {s['attended']} | "
                f"{s['conducted']} | **{_pct_str(s['percentage'])}** | {badge} |"
            )
    return "\n".join(lines)


def _subjects_block(rows: list, semester) -> str:
    heading = f"### 📚 Your Semester {semester} Subjects" if semester else "### 📚 Your Current Subjects"
    lines = [heading, "", "| Code | Subject | Type | Credits | Faculty |", "| :--- | :--- | :--- | :--- | :--- |"]
    for s in rows:
        lines.append(
            f"| `{s['subject_code']}` | **{s['subject_name']}** | {_cell(s['type'])} | "
            f"{_cell(s['credits'])} | {_cell(s['faculty'])} |"
        )
    lines.append(f"\n*{len(rows)} subject(s) registered.*")
    return "\n".join(lines)


def _faculty_block(rows: list) -> str:
    lines = ["### 👩‍🏫 Your Faculty", "", "| Subject | Faculty | Designation | Room | Office Hours |",
             "| :--- | :--- | :--- | :--- | :--- |"]
    for f in rows:
        lines.append(
            f"| {_cell(f['subject_name'])} | **{f['faculty_name']}** | {_cell(f['designation'])} | "
            f"`{_cell(f['room'])}` | {_cell(f['office_hours'])} |"
        )
    return "\n".join(lines)


def _marks_block(rows: list, semester) -> str:
    heading = f"### 📝 Internal Marks - Semester {semester}" if semester else "### 📝 Internal Marks"
    lines = [heading, "", "| Code | Subject | Mid-1 | Mid-2 | Assignment | Lab | Best Average | Out Of |",
             "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"]
    for m in rows:
        lines.append(
            f"| `{m['subject_code']}` | {m['subject_name']} | {_cell(m['mid_1'])} | {_cell(m['mid_2'])} | "
            f"{_cell(m['assignment'])} | {_cell(m['lab_internal'])} | **{_cell(m['best_mid_average'])}** | "
            f"{_cell(m['out_of'])} |"
        )
    return "\n".join(lines)


def _exams_block(rows: list) -> str:
    lines = ["### 🗓️ Your Examination Schedule", "", "| Subject | Examination | Date | Time | Room | Hall Ticket |",
             "| :--- | :--- | :--- | :--- | :--- | :--- |"]
    for e in rows:
        lines.append(
            f"| **{e['subject_name']}** (`{e['subject_code']}`) | {_cell(e['exam'])} | {_cell(e['date'])} | "
            f"{_cell(e['time'])} | `{_cell(e['room'])}` | {_cell(e['hall_ticket'])} |"
        )
    return "\n".join(lines)


def _assignments_block(rows: list) -> str:
    lines = ["### 📄 Your Assignments", "", "| Subject | Assignment | Due | Status | Max Marks |",
             "| :--- | :--- | :--- | :--- | :--- |"]
    for a in rows:
        icon = "🟡" if (a.get("status") or "").lower() == "pending" else "✅"
        lines.append(
            f"| {a['subject_name']} | {a['title']} | {_cell(a['due_date'])} | {icon} {_cell(a['status'])} | "
            f"{_cell(a['out_of'])} |"
        )
    return "\n".join(lines)


def _results_block(section: dict) -> str:
    lines = ["### 🎓 Your Semester Examination Results"]
    lines.append(f"- **Semester:** Semester {section['semester']} ({section['examination']})")
    lines.append(f"- **SGPA:** `{section['sgpa']}` | **Cumulative CGPA:** `{section['cgpa']}`")
    lines.append(f"- **Total Credits Earned:** `{section['credits_earned']}`")

    if section.get("backlogs"):
        lines.append(f"\n> ⚠️ **Backlog Notice:** you have **{section['backlogs']}** uncleared course(s).")
    else:
        lines.append("\n> 🎉 **Status:** all subjects cleared with zero backlogs!")

    lines.append("\n#### 📋 Course Grades:")
    lines.append("| Course Code | Course Title | Grade | Grade Points | Credits | Status |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for s in section["subjects"]:
        icon = "✅" if s["result"] == "PASS" else "❌"
        lines.append(
            f"| `{s['subject_code']}` | {s['subject_name']} | **{s['grade']}** | {s['grade_points']} | "
            f"{s['credits']} | {icon} {s['result']} |"
        )
    return "\n".join(lines)


def _backlogs_block(rows: list) -> str:
    lines = ["### 🔁 Your Backlogs", "", "| Code | Subject | Semester | Examination | Attempts | Status |",
             "| :--- | :--- | :--- | :--- | :--- | :--- |"]
    for b in rows:
        lines.append(
            f"| `{b['subject_code']}` | {b['subject_name']} | {_cell(b['semester'])} | "
            f"{_cell(b['examination'])} | {_cell(b['attempts'])} | {_cell(b['status'])} |"
        )
    return "\n".join(lines)


def _timetable_block(section: dict) -> str:
    branch, sec = section.get("branch"), section.get("section")
    suffix = f" (Branch: {branch} - Section {sec})" if branch else ""
    lines = [f"### 🗓️ Your Class Timetable{suffix}"]

    if not section.get("days"):
        note = section.get("note", "no scheduled lectures")
        lines.append(f"\n*Currently {note}.*")
        return "\n".join(lines)

    for day in section["days"]:
        lines.append(f"\n**{day['day']}:**")
        if not day["periods"]:
            lines.append("- *No scheduled lectures.*")
            continue
        lines.append("| Period | Time | Subject | Faculty | Room |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for p in day["periods"]:
            lines.append(
                f"| {p['period']} | {p['time']} | {p['subject']} | {p['faculty']} | `{p['room']}` |"
            )
    return "\n".join(lines)


def _profile_block(identity: dict, semester, sections: dict) -> str:
    rows = [
        ("Name", identity.get("name")),
        ("Roll Number", identity.get("roll_number")),
        ("Course", identity.get("course")),
        ("Department", identity.get("department")),
        ("Year", identity.get("year")),
        ("Current Semester", f"Semester {semester}" if semester else None),
        ("Section", identity.get("section")),
        ("Academic Year", identity.get("academic_year")),
        ("Subjects Registered", sections.get("subject_count")),
        ("Latest SGPA", sections.get("latest_sgpa")),
        ("Cumulative CGPA", sections.get("latest_cgpa")),
        ("Pending Backlogs", sections.get("pending_backlogs")),
    ]
    summary = sections.get("attendance_summary")
    if summary:
        flag = "🔴 below 75%" if summary.get("below_required_75") else "🟢 safe"
        rows.append(("Overall Attendance", f"{_pct_str(summary['overall_percentage'])} ({flag})"))

    lines = ["### 🎓 Your Academic Profile", "", "| Field | Value |", "| :--- | :--- |"]
    lines.extend(f"| {label} | {_cell(value)} |" for label, value in rows if value is not None)
    return "\n".join(lines)


def _notifications_block(rows: list) -> str:
    lines = ["### 📢 Academic Notifications", "", "| Priority | Title | Category | Date |",
             "| :--- | :--- | :--- | :--- |"]
    for n in rows:
        priority_icon = "🔴" if n.get("priority") == "Urgent" else "🟡" if n.get("priority") == "Important" else "ℹ️"
        lines.append(
            f"| {priority_icon} {_cell(n.get('priority'))} | **{_cell(n.get('title'))}** | {_cell(n.get('category'))} | {_cell(n.get('date'))} |"
        )
        if n.get("message"):
            lines.append(f"> 💬 {n['message']}\n")
    return "\n".join(lines)


# Rendered in this order when a question pulls in more than one section
_BUNDLE_RENDERERS = {
    "attendance": lambda value, identity, semester, sections: _attendance_block(value, identity.get("roll_number")),
    "current_semester_subjects": lambda value, identity, semester, sections: _subjects_block(value, semester),
    "faculty": lambda value, identity, semester, sections: _faculty_block(value),
    "internal_marks": lambda value, identity, semester, sections: _marks_block(value, semester),
    "examination_schedule": lambda value, identity, semester, sections: _exams_block(value),
    "assignments": lambda value, identity, semester, sections: _assignments_block(value),
    "latest_results": lambda value, identity, semester, sections: _results_block(value),
    "backlogs": lambda value, identity, semester, sections: _backlogs_block(value),
    "timetable": lambda value, identity, semester, sections: _timetable_block(value),
    "academic_notifications": lambda value, identity, semester, sections: _notifications_block(value),
}

PROFILE_SECTION_KEYS = ("subject_count", "latest_sgpa", "latest_cgpa", "pending_backlogs", "attendance_summary")


def format_context_bundle(bundle) -> str:
    """Renders a StudentContextBundle as the personal part of an answer."""
    identity, sections, semester = bundle.identity, bundle.sections, bundle.current_semester

    blocks = [
        render(sections[name], identity, semester, sections)
        for name, render in _BUNDLE_RENDERERS.items()
        if sections.get(name)
    ]

    if any(key in sections for key in PROFILE_SECTION_KEYS):
        blocks.insert(0, _profile_block(identity, semester, sections))

    blocks.extend(f"> ℹ️ {note}" for note in bundle.notes)

    blocks.append(f"*Source: {bundle.source or 'Anvaya ERP'}*")

    return "\n\n".join(blocks)
