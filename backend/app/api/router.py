"""
API Router Aggregator for Version 1.
"""

from fastapi import APIRouter
from backend.app.api.v1 import chat, auth, holidays, health, student

api_router = APIRouter()
api_router.include_router(chat.router)
api_router.include_router(auth.router)
api_router.include_router(student.router)
api_router.include_router(holidays.router)
api_router.include_router(health.router)
