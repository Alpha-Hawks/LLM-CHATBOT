"""
Root entrypoint for FastAPI Application (Vercel, Render, Local).
Exposes the FastAPI instance 'app'.
"""
import os
import sys
import traceback

# Ensure repository root is on sys.path
ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from backend.app.main import app
except Exception as exc:
    err_tb = traceback.format_exc()
    print("FATAL ERROR LOADING backend.app.main:\n" + err_tb, file=sys.stderr)
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    app = FastAPI(title="MLRITM Chatbot - Diagnostic Mode")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
    async def _diagnostic_handler(path: str = ""):
        return PlainTextResponse(
            f"STARTUP ERROR:\n{type(exc).__name__}: {exc}\n\nTRACEBACK:\n{err_tb}",
            status_code=500,
        )

__all__ = ["app"]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
