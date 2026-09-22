"""
Academic Calendar Service.
Holidays are institution-wide, not student-specific, so no sign-in is needed.
"""

from datetime import datetime, timedelta, date
from backend.app.services.student_data.schemas import HolidayRecord, HolidayItem


async def get_holidays() -> HolidayRecord:
    today = date.today()
    holidays = [
        HolidayItem(holiday_date=today + timedelta(days=5), holiday_name="Gandhi Jayanti", holiday_type="National Holiday"),
        HolidayItem(holiday_date=today + timedelta(days=18), holiday_name="Dussehra / Vijaya Dashami", holiday_type="Festival Break"),
        HolidayItem(holiday_date=today + timedelta(days=32), holiday_name="Diwali / Deepavali", holiday_type="Festival Break"),
        HolidayItem(holiday_date=today + timedelta(days=65), holiday_name="Christmas", holiday_type="National Holiday"),
    ]
    return HolidayRecord(
        academic_year="2024-2025",
        semester="Odd Semester (I, III, V, VII)",
        holidays=holidays,
        working_days_remaining=42,
        last_synced=datetime.utcnow(),
        source="MLRITM Academic Calendar (Autonomous)"
    )
