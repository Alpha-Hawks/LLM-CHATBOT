"""
Clean OpenAIService Interface for MLRITM Student AI Assistant.
Provides server-side OpenAI integration with strict boundary enforcement,
anti-hallucination system prompt, and deterministic offline fallback.
"""

from backend.app.llm.openai_client import (
    OpenAIService,
    openai_service,
)

__all__ = ["OpenAIService", "openai_service"]
