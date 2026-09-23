"""
Root entrypoint for FastAPI Application (Vercel, Render, Local).
Exposes the FastAPI instance 'app'.
"""
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from backend.app.main import app  # noqa: E402

__all__ = ["app"]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
