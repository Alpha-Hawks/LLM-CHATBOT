"""
Academic Calendar & Holidays API Endpoints.
Provides queries for:
- Upcoming holidays
- Next immediate holiday
- Is tomorrow a holiday?
- Working days remaining in current semester
"""

from datetime import date, timedelta
from fastapi import APIRouter
from backend.app.services.academic_calendar import get_holidays
from backend.app.services.response_formatter import format_holiday_response

router = APIRouter(prefix="/holidays", tags=["Academic Calendar & Holidays"])


@router.get("/")
async def get_all_holidays():
    """Returns official holiday list for the current academic semester."""
    record = await get_holidays()
    return {
        "data": record,
        "formatted_markdown": format_holiday_response(record)
    }


@router.get("/is-tomorrow-holiday")
async def is_tomorrow_holiday():
    """Checks if tomorrow is a declared holiday or working day."""
    record = await get_holidays()
    tomorrow = date.today() + timedelta(days=1)
    
    match = next((h for h in record.holidays if h.holiday_date == tomorrow), None)
    if match:
        return {
            "is_holiday": True,
            "holiday_name": match.holiday_name,
            "holiday_type": match.holiday_type,
            "message": f"Yes, tomorrow ({tomorrow.strftime('%d %b %Y')}) is a holiday for {match.holiday_name}."
        }
    return {
        "is_holiday": False,
        "holiday_name": None,
        "message": f"No, tomorrow ({tomorrow.strftime('%d %b %Y')}) is a regular working day according to the academic calendar."
    }


@router.get("/next")
async def get_next_holiday():
    """Returns the next upcoming college holiday."""
    record = await get_holidays()
    today = date.today()
    future = [h for h in record.holidays if h.holiday_date >= today]
    future.sort(key=lambda x: x.holiday_date)

    if future:
        next_h = future[0]
        days_away = (next_h.holiday_date - today).days
        return {
            "next_holiday": next_h,
            "days_away": days_away,
            "message": f"The next holiday is {next_h.holiday_name} on {next_h.holiday_date.strftime('%d %B %Y')} ({days_away} days away)."
        }
    return {"message": "No upcoming holidays listed in current semester calendar."}
