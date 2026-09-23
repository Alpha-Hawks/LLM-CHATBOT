"""
Vercel Serverless Function entrypoint for FastAPI.
Exposes 'app' for Vercel's zero-config Python runtime.
"""
import os
import sys

# Ensure repository root is on sys.path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.app.main import app

__all__ = ["app"]
