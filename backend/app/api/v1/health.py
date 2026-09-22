"""
Health and Diagnostic Endpoints for Kubernetes / Docker Monitoring.
"""

from fastapi import APIRouter
from datetime import datetime
from backend.app.core.config import settings
from backend.app.services.identity.registry import get_identity_provider
from backend.app.services.student_data.providers import get_student_data_provider

router = APIRouter(prefix="/health", tags=["Health & Diagnostics"])


@router.get("/live")
async def liveness_probe():
    """Confirms backend service process is alive."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@router.get("/ready")
async def readiness_probe():
    """Verifies backend dependencies and configuration."""
    provider = get_identity_provider()
    return {
        "status": "ready",
        "timestamp": datetime.utcnow().isoformat(),
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "identity_provider": provider.name if provider else "none",
        "student_data_provider": get_student_data_provider().name
    }
