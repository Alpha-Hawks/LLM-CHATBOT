"""
Chat API Endpoints.

The full personalization pipeline for one question:

    intent router          sees the question only - never an identity, never a record
        |
    authorization          session + DPDP consent, decided in the backend  (services/authorization.py)
        |
    context engine         picks the few authorized fields this question needs  (services/context_engine.py)
        |
    AI response engine     personal record + official knowledge -> one answer  (llm/ai_response_engine.py)

General questions (regulations, fees, the academic calendar) skip the first two stages entirely
and keep working whether or not anyone is signed in.
"""

import asyncio
import json
from typing import Any, List, Optional, Union

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.llm.ai_response_engine import AIResponseEngine
from backend.app.llm.intent_router import IntentEnum, IntentRouter
from backend.app.llm.rag_engine import RAGEngine
from backend.app.services import audit, context_engine
from backend.app.services.academic_calendar import get_holidays
from backend.app.services.authorization import (
    StudentAccessDenied,
    ensure_can_read_records,
    get_student_context,
)
from backend.app.services.identity.base import StudentContext
import re
from backend.app.services.live_data import live_data_service
from backend.app.services.response_formatter import format_holiday_response

router = APIRouter(prefix="/chat", tags=["Chat & Academic Advising"])

# Initialize Singletons
intent_router = IntentRouter()
rag_engine = RAGEngine()
ai_engine = AIResponseEngine()

# Personal intents -> (audit category, the sentence that introduces the record)
PERSONAL_INTENT_LEAD_INS = {
    IntentEnum.ATTENDANCE: ("attendance", "Here is your current attendance summary:"),
    IntentEnum.RESULTS: ("results", "Here are your latest semester examination results:"),
    IntentEnum.MARKS: ("marks", "Here are your internal assessment marks:"),
    IntentEnum.TIMETABLE: ("timetable", "Here is your scheduled class timetable:"),
    IntentEnum.SUBJECTS: ("subjects", "Here are the subjects you are registered for:"),
    IntentEnum.FACULTY: ("faculty", "Here are the faculty members teaching you:"),
    IntentEnum.EXAMS: ("exams", "Here is your examination schedule:"),
    IntentEnum.ASSIGNMENTS: ("assignments", "Here are your assignments:"),
    IntentEnum.PROFILE: ("profile", "Here is your academic profile:"),
    IntentEnum.FAQ_RAG: ("attendance", None),
}

ACCESS_DENIED_ANSWERS = {
    "not_signed_in": (
        "To see your personal academic records, sign in with your Anvaya account "
        f"({settings.ANVAYA_BASE_URL}) using the **Login with Anvaya** button. "
        "I never ask for your Anvaya password."
    ),
    "consent_required": (
        "Before I show your academic records, please review the DPDP Act 2023 notice and give your consent."
    ),
    "ownership_mismatch": (
        "I couldn't show that record because it did not belong to your account. The problem has been logged."
    ),
}

RECORDS_UNAVAILABLE_SUFFIX = (
    f"\n\nYou can check this directly on [Anvaya]({settings.ANVAYA_BASE_URL}). "
    "I can still answer general questions about MLRITM regulations, fees and the academic calendar."
)


class ChatRequest(BaseModel):
    message: str = Field(..., examples=["What is my attendance percentage?"])
    stream: bool = Field(default=False)


class ChatResponse(BaseModel):
    answer: str
    intent: str
    confidence: float
    source: str
    requires_auth: bool = False
    requires_consent: bool = False
    citations: Optional[Union[str, List[Any]]] = None
    used_personal_data: bool = False
    used_knowledge_base: bool = False
    current_semester: Optional[int] = None


async def _personal_answer(
    db: AsyncSession,
    message: str,
    student: StudentContext,
    intent: IntentEnum,
    confidence: float,
) -> ChatResponse:
    """Answers from the signed-in student's own record, blended with policy when it is advice."""
    category, lead_in = PERSONAL_INTENT_LEAD_INS.get(intent, ("profile", None))

    LIVE_FEATURE_BY_INTENT = {
        IntentEnum.ATTENDANCE: "attendance",
        IntentEnum.MARKS: "internal_marks",
        IntentEnum.RESULTS: "results",
        IntentEnum.TIMETABLE: "timetable",
        IntentEnum.EXAMS: "exams",
        IntentEnum.PROFILE: "profile",
    }

    detected_feature = None
    if re.search(r"\b(my fees?|fee status|fee payment|dues|balance|pending fee)\b", message, re.IGNORECASE):
        detected_feature = "fees"
    elif intent in LIVE_FEATURE_BY_INTENT:
        detected_feature = LIVE_FEATURE_BY_INTENT[intent]

    # When live data mode is active (not sample student provider)
    if detected_feature and getattr(settings, "STUDENT_DATA_PROVIDER", "none") != "sample":
        roll = student.roll_number or student.student_key
        live_res = await live_data_service.get_feature_data(roll, detected_feature)

        combined_answer = live_res.answer
        used_rag = False
        if context_engine.needs_official_policy(intent, message):
            rag_res = rag_engine.answer_query(message)
            if rag_res and not rag_res.get("is_fallback"):
                combined_answer = f"{live_res.answer}\n\n**MLRITM Academic Policy Guidance:**\n{rag_res['answer']}"
                used_rag = True

        await audit.record(
            db, "RECORD_ACCESS", student=student, intent=intent.value,
            data_category=detected_feature, detail=f"mode={live_res.mode}, status={live_res.status}",
        )
        return ChatResponse(
            answer=combined_answer,
            intent=intent.value,
            confidence=confidence,
            source=live_res.source,
            used_personal_data=live_res.is_live,
            used_knowledge_base=used_rag,
        )

    context = await context_engine.build(db, student, intent, message)

    if not context.available:
        await audit.record(
            db, "RECORD_ACCESS", student=student, intent=intent.value,
            data_category=category, status="UNAVAILABLE", detail=context.reason,
        )
        return ChatResponse(
            answer=(context.message or ACCESS_DENIED_ANSWERS["ownership_mismatch"]) + RECORDS_UNAVAILABLE_SUFFIX,
            intent=intent.value,
            confidence=confidence,
            source="Student Records (Unavailable)",
        )

    bundle = context.bundle

    # "What should I do about my shortage?" needs the regulation next to the student's figures
    knowledge = rag_engine.answer_query(message) if bundle.needs_policy else None

    result = ai_engine.answer(message, bundle=bundle, knowledge=knowledge, lead_in=lead_in)

    await audit.record(
        db, "RECORD_ACCESS", student=student, intent=intent.value,
        data_category=category, detail=f"sections={','.join(bundle.sections) or 'none'}",
    )

    return ChatResponse(
        answer=result.answer,
        intent=intent.value,
        confidence=confidence,
        source=bundle.source or "Anvaya ERP",
        citations=result.citations,
        used_personal_data=result.used_personal_data,
        used_knowledge_base=result.used_knowledge_base,
        current_semester=bundle.current_semester,
    )


async def process_chat_query(
    message: str,
    student: Optional[StudentContext] = None,
    db: Optional[AsyncSession] = None,
) -> ChatResponse:
    """Core execution logic. `student` comes only from the server-side session."""
    route_res = intent_router.route(message)
    intent = route_res.intent

    # =========================================================================
    # Privacy Guard (DPDP Act 2023): Refuse inquiries into other students' records
    # =========================================================================
    if student is not None:
        matches = re.findall(r"\b([0-9]{2}[0-9A-Za-z]{8})\b", message)
        my_roll = (student.roll_number or "").strip().upper()
        if any(m.upper() != my_roll for m in matches):
            return ChatResponse(
                answer=(
                    "I cannot look up or share academic records for other students. "
                    "Under MLRITM privacy policy and India's DPDP Act 2023, each student "
                    "can only access their own personal academic records."
                ),
                intent=intent.value,
                confidence=route_res.confidence,
                source="Privacy Guard (DPDP Act 2023)",
            )

    # =========================================================================
    # PERSONAL PATH - the student's own academic record
    # Authorization is decided here, in the backend, before anything is loaded. The router's
    # output selects a category only; its parameters are never used for identity.
    # =========================================================================
    if db is not None and context_engine.requires_personal_data(intent, message):
        try:
            authorized = ensure_can_read_records(student)
        except StudentAccessDenied as denied:
            # A general question asked personally still deserves the general answer
            if intent == IntentEnum.FAQ_RAG:
                return _knowledge_answer(message, intent, route_res.confidence)
            return ChatResponse(
                answer=ACCESS_DENIED_ANSWERS[denied.reason],
                intent=intent.value,
                confidence=route_res.confidence,
                source="Student Records Authorization",
                requires_auth=denied.reason == "not_signed_in",
                requires_consent=denied.reason == "consent_required",
            )

        try:
            return await _personal_answer(db, message, authorized, intent, route_res.confidence)
        except StudentAccessDenied as denied:
            return ChatResponse(
                answer=ACCESS_DENIED_ANSWERS[denied.reason],
                intent=intent.value,
                confidence=route_res.confidence,
                source="Student Records Authorization",
                requires_auth=denied.reason == "not_signed_in",
                requires_consent=denied.reason == "consent_required",
            )

    # Personal intent, but this route has no database session to authorize against
    if context_engine.requires_personal_data(intent, message) and intent != IntentEnum.FAQ_RAG:
        return ChatResponse(
            answer=ACCESS_DENIED_ANSWERS["not_signed_in"],
            intent=intent.value,
            confidence=route_res.confidence,
            source="Student Records Authorization",
            requires_auth=True,
        )

    # =========================================================================
    # ACADEMIC CALENDAR & HOLIDAYS (institution-wide, no sign-in needed)
    # =========================================================================
    if intent == IntentEnum.HOLIDAYS:
        record = await get_holidays()
        return ChatResponse(
            answer=(
                "Here is the official MLRITM academic calendar and holiday schedule:\n\n"
                f"{format_holiday_response(record)}"
            ),
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Academic Calendar",
            used_knowledge_base=True,
        )

    # =========================================================================
    # STATIC KNOWLEDGE (RAG over the official regulations)
    # =========================================================================
    if intent == IntentEnum.FAQ_RAG:
        return _knowledge_answer(message, intent, route_res.confidence)

    # =========================================================================
    # CONVERSATIONAL PLEASANTRIES
    # =========================================================================
    if intent == IntentEnum.SMALL_TALK:
        return ChatResponse(
            answer=_greeting(student),
            intent=intent.value,
            confidence=route_res.confidence,
            source="System Greeting",
        )

    # =========================================================================
    # ESCALATION & GRIEVANCE
    # =========================================================================
    if intent == IntentEnum.ESCALATE:
        return ChatResponse(
            answer=(
                "🚨 **Campus Assistance & Grievance Contacts:**\n\n"
                "- **Anti-Ragging Squad Hotline:** +91-9959663366 / anti-ragging@mlritm.ac.in\n"
                "- **Student Grievance Cell:** Room 104, Administrative Block\n"
                "- **Academic Examination Branch:** exambranch@mlritm.ac.in\n"
                "- **Campus Emergency / Security:** +91-8418-204066\n\n"
                "Your safety and academic well-being are paramount. Please contact the concerned office immediately."
            ),
            intent=intent.value,
            confidence=1.0,
            source="MLRITM Student Support Cell",
        )

    # =========================================================================
    # OUT OF SCOPE
    # =========================================================================
    return ChatResponse(
        answer=(
            "I assist specifically with MLRITM academic regulations, B.Tech courses, fees, "
            "attendance, and your Anvaya student records. I cannot assist with non-academic or "
            "off-topic requests. Feel free to ask about your syllabus, exam rules, or attendance percentage!"
        ),
        intent=intent.value,
        confidence=route_res.confidence,
        source="Scope Guard",
    )


def _knowledge_answer(message: str, intent: IntentEnum, confidence: float) -> ChatResponse:
    """A purely institutional answer from the official knowledge base."""
    rag_res = rag_engine.answer_query(message)
    return ChatResponse(
        answer=rag_res["answer"],
        intent=intent.value,
        confidence=confidence,
        source="MLRITM Autonomous Academic Regulations",
        citations=rag_res.get("citations"),
        used_knowledge_base=not rag_res.get("is_fallback", False),
    )


def _greeting(student: Optional[StudentContext]) -> str:
    """Greets a signed-in student by name; otherwise explains how to connect Anvaya."""
    if student is not None:
        name = student.display_name or student.roll_number or "there"
        return (
            f"Hello {name}! I am your MLRITM Student AI Assistant. "
            "I can look up your attendance, subjects, internal marks, faculty, timetable, exams "
            "and results for this semester, and answer questions about MLRITM regulations, fees "
            "and the academic calendar."
        )
    return (
        "Hello! I am the official MLRITM Student AI Assistant. "
        "I can help with course regulations, fee structures and scholarships right away. "
        "Use **Login with Anvaya** to also ask about your own attendance, subjects, marks, "
        "timetable and results."
    )


@router.post("/", response_model=ChatResponse)
async def chat_endpoint(
    req: ChatRequest,
    student: Optional[StudentContext] = Depends(get_student_context),
    db: AsyncSession = Depends(get_db),
):
    """Standard REST Chat Endpoint."""
    return await process_chat_query(req.message, student, db)


@router.post("/stream")
async def chat_stream_endpoint(
    req: ChatRequest,
    student: Optional[StudentContext] = Depends(get_student_context),
    db: AsyncSession = Depends(get_db),
):
    """Server-Sent Events (SSE) Streaming Endpoint."""

    async def event_generator():
        res = await process_chat_query(req.message, student, db)
        words = res.answer.split(" ")
        for index in range(0, len(words), 3):
            chunk = " ".join(words[index:index + 3]) + " "
            data = json.dumps({
                "token": chunk,
                "intent": res.intent,
                "source": res.source,
                "requires_auth": res.requires_auth,
                "requires_consent": res.requires_consent,
                "used_personal_data": res.used_personal_data,
                "current_semester": res.current_semester,
            })
            yield f"data: {data}\n\n"
            await asyncio.sleep(0.03)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
