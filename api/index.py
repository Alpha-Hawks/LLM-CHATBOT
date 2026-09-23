"""
Vercel Serverless Function entrypoint for FastAPI backend.
Exposes the FastAPI instance 'app' for Vercel's Python runtime.
"""
import os
import sys

# Ensure repository root is in sys.path so backend modules can be imported
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.app.main import app  # noqa: E402

__all__ = ["app"]
