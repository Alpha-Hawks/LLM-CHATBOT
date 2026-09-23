"""
Main Application Entry Point for FastAPI Backend.
Configures CORS, lifespan events, API routers, and static file serving for PWA and widget.
"""

import os
import sys
from contextlib import asynccontextmanager

# Ensure repository root is in sys.path when running script directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.core.config import settings
from backend.app.core.http_security import SecurityHeadersMiddleware
from backend.app.core.security import assert_secrets_fit_for_student_data, weak_secret_problems
from backend.app.api.router import api_router
from backend.app.api.v1 import student as student_router_module
from backend.app.db.session import init_db
from backend.app.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing MLRITM Academic Advising Service...")
    try:
        assert_secrets_fit_for_student_data()
    except Exception as e:
        logger.warning(f"Configuration notice: {e}")

    if settings.ENVIRONMENT != "development":
        for problem in weak_secret_problems():
            logger.warning(f"Configuration: {problem}.")
        if not settings.ENFORCE_HTTPS:
            logger.warning("Configuration: ENFORCE_HTTPS is off; serve students only over HTTPS.")

    # Initialize DB schemas safely
    try:
        await init_db()
        logger.info("Database schemas verified.")
    except Exception as e:
        logger.warning(f"Database schemas non-fatal warning: {e}")

    # Heavy background sync of 256 faculty rows runs locally/workers, skipped during serverless cold starts
    is_serverless = bool(
        os.getenv("VERCEL")
        or os.getenv("VERCEL_ENV")
        or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
    )
    if not is_serverless:
        try:
            from backend.app.db.session import AsyncSessionLocal
            from backend.app.services.mlritm_sync import mlritm_sync_service
            async with AsyncSessionLocal() as session:
                await mlritm_sync_service.synchronize(session, force_live=False)
            logger.info("Official MLRITM Knowledge Base synchronized.")
        except Exception as e:
            logger.warning(f"Could not synchronize MLRITM knowledge base on startup: {e}")
    yield
    logger.info("Shutting down service...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="LLM-Powered Academic Advising Assistant for MLRITM with Anvaya ERP Live Integration.",
    version="1.0.0",
    lifespan=lifespan,
    debug=True,
)

# CORS Policy: Restricted to Anvaya portal, local origins, and all *.vercel.app deployments
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=r"^https:\/\/.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers, no-store on API responses, and optional HTTPS enforcement
app.add_middleware(SecurityHeadersMiddleware)

# Mount API Routers
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(student_router_module.router, prefix="/api")

# Mount PWA frontend if directory exists
frontend_pwa_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "pwa")
if os.path.exists(frontend_pwa_dir):
    app.mount("/app", StaticFiles(directory=frontend_pwa_dir, html=True), name="pwa")

# Mount Widget script directory
widget_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "widget")
if os.path.exists(widget_dir):
    app.mount("/widget", StaticFiles(directory=widget_dir), name="widget")

# Mount Anvaya Portal directory
anvaya_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "anvaya")
if os.path.exists(anvaya_dir):
    app.mount("/anvaya", StaticFiles(directory=anvaya_dir, html=True), name="anvaya")


@app.get("/health")
@app.get("/api/health")
async def health():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "serverless": bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV")),
    }


@app.get("/")
async def root(request: Request):
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return {
            "service": settings.PROJECT_NAME,
            "status": "online",
            "pwa_url": "/app/",
            "api_docs": "/docs",
            "health": "/health",
            "anvaya_endpoint": "https://anvaya.mlritm.ac.in"
        }
    return RedirectResponse(url="/app/", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)

