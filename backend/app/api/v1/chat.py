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
import re
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.llm.ai_response_engine import AIResponseEngine
from backend.app.llm.intent_router import IntentEnum, IntentRouter, RouterMode
from backend.app.llm.openai_client import openai_service
from backend.app.llm.rag_engine import RAGEngine
from backend.app.llm.college_kb import college_kb
from backend.app.services.web_search import web_search_service
from backend.app.services import audit, context_engine
from backend.app.services.academic_calendar import get_holidays
from backend.app.services.authorization import (
    StudentAccessDenied,
    ensure_can_read_records,
    get_student_context,
)
from backend.app.services.identity.base import StudentContext
from backend.app.services.live_data import live_data_service
from backend.app.services.response_formatter import format_holiday_response
from backend.app.services.adaptive_knowledge import adaptive_knowledge_service
from backend.app.services.faculty_service import faculty_service
from backend.app.services.query_normalizer import query_normalizer, DEPARTMENT_REGISTRY
from backend.app.db.models import QueryPatternLog

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
    session_id: Optional[str] = None
    conversation_history: Optional[List[Dict[str, str]]] = None


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
    cards: Optional[List[Dict[str, Any]]] = None
    mode: Optional[str] = RouterMode.COLLEGE_MODE.value


def _extract_specific_student_attribute(message: str) -> Optional[tuple[str, str]]:
    """Extracts specific student profile field requested, or None for general profile."""
    q = message.lower()
    if re.search(r"\b(branch|department)\b", q):
        return ("branch", "Branch / Department")
    if re.search(r"\b(semester|which semester|current semester|what semester)\b", q):
        return ("semester_display", "Current Semester")
    if re.search(r"\b(year of study|which year am i)\b", q):
        return ("year_of_study", "Year of Study")
    if re.search(r"\b(admission year|joining year|which year did i join)\b", q):
        return ("admission_year", "Admission Year")
    if re.search(r"\b(student email|my email|official email)\b", q):
        return ("student_email", "Official Student Email")
    if re.search(r"\b(student mobile|my mobile|my phone)\b", q):
        return ("student_mobile", "Student Mobile Number")
    if re.search(r"\b(father.*(profession|job|work)|parent.*profession)\b", q):
        return ("parent_profession", "Father's / Parent's Profession")
    if re.search(r"\b(parent.*income|father.*income|annual income)\b", q):
        return ("parent_income", "Annual Parent Income")
    if re.search(r"\b(father.*(mobile|phone))\b", q):
        return ("father_mobile", "Father's Mobile Number")
    if re.search(r"\b(father.*name)\b", q) and not re.search(r"(mobile|phone|profession|job|work)", q):
        return ("father_name", "Father's Name")
    if re.search(r"\b(mother.*name)\b", q) and not re.search(r"(mobile|phone)", q):
        return ("mother_name", "Mother's Name")
    if re.search(r"\b(mother.*(mobile|phone))\b", q):
        return ("mother_mobile", "Mother's Mobile Number")
    if re.search(r"\b(scholarship)\b", q):
        return ("scholarship_type", "Scholarship Status")
    if re.search(r"\b(admission category|quota|convener|management)\b", q):
        return ("admission_category", "Admission Category")
    if re.search(r"\b(date of birth|dob)\b", q):
        return ("date_of_birth", "Date of Birth")
    if re.search(r"\b(caste)\b", q):
        return ("caste_name", "Caste Name")
    if re.search(r"\b(roll number|roll no|my roll)\b", q):
        return ("roll_number", "Roll Number")
    return None


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
        IntentEnum.SUBJECTS: "subjects",
        IntentEnum.FACULTY: "faculty",
        IntentEnum.TIMETABLE: "timetable",
        IntentEnum.EXAMS: "exams",
        IntentEnum.PROFILE: "profile",
    }

    detected_feature = None
    if re.search(r"\b(my fees?|fee status|fee payment|dues|balance|pending fee)\b", message, re.IGNORECASE):
        detected_feature = "fees"
    elif intent in LIVE_FEATURE_BY_INTENT:
        detected_feature = LIVE_FEATURE_BY_INTENT[intent]

    # Check for specific profile attribute inquiry (e.g. branch, admission year, father's profession)
    if (detected_feature == "profile" or intent == IntentEnum.PROFILE):
        attr_info = _extract_specific_student_attribute(message)
        if attr_info:
            attr_key, attr_label = attr_info
            roll = student.roll_number or student.student_key
            from backend.app.services.student_service import getStudentByRollNumberSync
            stored = getStudentByRollNumberSync(roll)
            if isinstance(stored, dict):
                val = stored.get(attr_key)
                if not val or str(val).strip().lower() in ("", "none", "null"):
                    val = "Not Available"
                
                from backend.app.services.openai_service import openai_service
                answer = await openai_service.answer_student_attribute(
                    student_query=message,
                    attribute_name=attr_label,
                    attribute_value=str(val).strip(),
                    roll_number=roll,
                )
                await audit.record(
                    db, "RECORD_ACCESS", student=student, intent=intent.value,
                    data_category="profile", detail=f"attribute={attr_key}",
                )
                return ChatResponse(
                    answer=answer,
                    intent=intent.value,
                    confidence=confidence,
                    source="MLRITM Student Master Database",
                    used_personal_data=True,
                )

    # When live data mode is active (not sample student provider and not fully-configured official Anvaya API)
    from backend.app.services.student_data.providers import get_student_data_provider, AnvayaApiStudentDataProvider
    active_provider = get_student_data_provider()
    official_api_ready = isinstance(active_provider, AnvayaApiStudentDataProvider) and active_provider.is_configured()

    if detected_feature and getattr(settings, "STUDENT_DATA_PROVIDER", "none") != "sample" and not official_api_ready:
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
    history: Optional[List[Dict[str, str]]] = None,
) -> ChatResponse:
    """Core execution logic. `student` comes only from the server-side session."""
    try:
        route_res = intent_router.route(message, history=history)
    except TypeError:
        route_res = intent_router.route(message)
    intent = route_res.intent
    mode = route_res.mode

    # Ambiguous short query handling (e.g. "english" -> clarification prompt)
    if intent == IntentEnum.AMBIGUOUS_CLARIFICATION:
        clarification_msg = route_res.parameters.get(
            "clarification_prompt",
            "Could you please clarify whether you are asking about faculty, courses, or college details?"
        )
        return ChatResponse(
            answer=clarification_msg,
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Intent Resolution Platform",
            mode=RouterMode.COLLEGE_MODE.value,
        )

    # Multi-question compound query check (e.g. "Who is the IT HOD and when is the next event?")
    if intent not in [IntentEnum.HYBRID_QUERY, IntentEnum.PROJECT_IDEAS]:
        sub_queries = college_kb.decompose_multi_question(message)
        if len(sub_queries) > 1:
            answers = []
            combined_cards = []
            for sub_q in sub_queries:
                sub_res = await process_chat_query(sub_q, student, db, history=history)
                answers.append(sub_res.answer)
                if sub_res.cards:
                    combined_cards.extend(sub_res.cards)
            combined_answer = "\n\n---\n\n".join(answers)
            return ChatResponse(
                answer=combined_answer,
                intent="MULTI_QUERY",
                confidence=0.95,
                source="MLRITM Official Knowledge Base",
                cards=combined_cards if combined_cards else None,
                used_knowledge_base=True,
                mode=RouterMode.COLLEGE_MODE.value,
            )

    # =========================================================================
    # Knowledge Poisoning Guard: Never accept unverified student assertions as official facts
    # =========================================================================
    if adaptive_knowledge_service.is_knowledge_poisoning_attempt(message):
        adaptive_res = await adaptive_knowledge_service.process_query(message, db) if db is not None else {}
        return ChatResponse(
            answer=adaptive_res.get("answer", (
                "I cannot accept or record unverified statements as official college information. "
                "Under MLRITM academic policy, any notice regarding holidays, schedule changes, "
                "or examinations must be verified through an official circular issued by the Principal "
                "or Controller of Examinations. I have not received an official notice confirming this statement."
            )),
            intent="KNOWLEDGE_INTEGRITY_GUARD",
            confidence=0.99,
            source="MLRITM Knowledge Integrity Guard",
            citations=["https://www.mlritm.ac.in/"],
            mode=RouterMode.COLLEGE_MODE.value,
        )

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
                mode=RouterMode.COLLEGE_MODE.value,
            )

    # =========================================================================
    # AUTHORITATIVE MLRITM FACULTY INTELLIGENCE & SHORT QUERY SYSTEM
    # =========================================================================
    if intent in [
        IntentEnum.FACULTY_HOD,
        IntentEnum.FACULTY_LIST,
        IntentEnum.FACULTY_SEARCH,
        IntentEnum.FACULTY_PROFILE,
        IntentEnum.FACULTY_COUNT,
        IntentEnum.FACULTY_SUBJECT,
        IntentEnum.FACULTY_RESEARCH,
    ]:
        params = route_res.parameters
        norm_q = params.get("normalized_query", message.lower())
        dept_code = params.get("department_code")
        dept_name = params.get("department")

        # Background Query Pattern Logging for Continuous ML Learning
        if db is not None:
            try:
                log_entry = QueryPatternLog(
                    raw_query=message,
                    normalized_query=norm_q,
                    predicted_intent=intent.value,
                    resolved_intent=intent.value,
                    entities_json=json.dumps(params),
                    confidence=route_res.confidence,
                    source_used="Official MLRITM Faculty Profile System",
                    success=True,
                )
                db.add(log_entry)
                await db.commit()
            except Exception:
                pass

        # -------------------------------------------------------------
        # 1. HOD Detection & Resolution
        # -------------------------------------------------------------
        if intent == IntentEnum.FACULTY_HOD:
            is_my_hod = bool(re.search(r"\b(my|our)\s+hod\b", message.lower()) or params.get("resolved_from_student_session"))
            if is_my_hod or not dept_code:
                if student is not None:
                    # 1. Lookup student from DB
                    if db is not None:
                        from backend.app.db.models import Student
                        from sqlalchemy import select, or_
                        stmt = select(Student).where(
                            or_(
                                Student.roll_number == student.roll_number,
                                Student.student_key == student.student_key,
                            )
                        )
                        student_rec = (await db.execute(stmt)).scalars().first()
                        if student_rec and (student_rec.branch or student_rec.course_name):
                            resolved_dept = query_normalizer.resolve_department(student_rec.branch or student_rec.course_name)
                            if resolved_dept:
                                dept_code, dept_name, _ = resolved_dept

                    # 2. Lookup branch from roll number format (e.g. 22R21A1201 -> 12 = IT, 22R21A0501 -> 05 = CSE)
                    if not dept_code and student.roll_number:
                        roll = student.roll_number.upper()
                        branch_code_map = {
                            "05": "CSE", "12": "IT", "66": "CSE-AI-ML", "67": "CSE-DATA-SCIENCE",
                            "62": "CSE-CYBER-SECURITY", "04": "ECE", "02": "EEE", "03": "MECHANICAL",
                            "01": "CIVIL", "E0": "MBA"
                        }
                        for b_code, d_code in branch_code_map.items():
                            if f"A{b_code}" in roll or f"E{b_code}" in roll:
                                dept_code = d_code
                                dept_name = DEPARTMENT_REGISTRY.get(d_code, {}).get("name", d_code)
                                break

                elif is_my_hod:
                    return ChatResponse(
                        answer=(
                            "To check your department's HOD automatically, please log in with your Anvaya account, "
                            "or specify your department (for example, 'CSE HOD', 'IT HOD', 'ECE HOD')."
                        ),
                        intent=intent.value,
                        confidence=route_res.confidence,
                        source="MLRITM Faculty Profile System",
                        mode=RouterMode.COLLEGE_MODE.value,
                    )

            dept_code = dept_code or "CSE"
            hod = await faculty_service.get_hod(dept_code, db)
            if not hod:
                return ChatResponse(
                    answer=f"Could not find official HOD records for {dept_name or dept_code} on the official MLRITM faculty page.",
                    intent=intent.value,
                    confidence=route_res.confidence,
                    source="MLRITM Official Faculty System",
                    mode=RouterMode.COLLEGE_MODE.value,
                )

            words = message.strip().split()
            is_short = len(words) <= 4 and not re.search(r"\b(qualification|qualifications|degree|experience|research|profile|detail)\b", message.lower())
            card_text = faculty_service.format_faculty_card(hod, is_short_query=is_short)

            return ChatResponse(
                answer=card_text,
                intent=intent.value,
                confidence=route_res.confidence,
                source="Official MLRITM Faculty Profile System (https://mlritm.ac.in/faculty-profile)",
                used_knowledge_base=True,
                cards=[hod],
                mode=RouterMode.COLLEGE_MODE.value,
            )

        # -------------------------------------------------------------
        # 2. Faculty Directory / List
        # -------------------------------------------------------------
        if intent == IntentEnum.FACULTY_LIST:
            dept_code = dept_code or "CSE"
            designation_filter = None
            if "assistant" in message.lower():
                designation_filter = "assistant"
            elif "associate" in message.lower():
                designation_filter = "associate"
            elif "professor" in message.lower():
                designation_filter = "professor"

            faculty_list = await faculty_service.get_faculty_by_department(dept_code, db, designation_filter)
            title = f"{dept_name or dept_code} Faculty"
            if designation_filter:
                title += f" ({designation_filter.capitalize()}s)"
            list_text = faculty_service.format_faculty_list(faculty_list, title=title)
            return ChatResponse(
                answer=list_text,
                intent=intent.value,
                confidence=route_res.confidence,
                source="Official MLRITM Faculty Profile System (https://mlritm.ac.in/faculty-profile)",
                used_knowledge_base=True,
                cards=faculty_list[:5],
                mode=RouterMode.COLLEGE_MODE.value,
            )

        # -------------------------------------------------------------
        # 3. Faculty Search & Individual Profile
        # -------------------------------------------------------------
        if intent in [IntentEnum.FACULTY_SEARCH, IntentEnum.FACULTY_PROFILE]:
            faculty_query = params.get("target_faculty") or params.get("faculty_query") or message
            focus = params.get("focus")

            results = await faculty_service.search_faculty(faculty_query, db, department_code=dept_code)
            if not results:
                return ChatResponse(
                    answer=(
                        f"I couldn't find an officially verified faculty record matching '{faculty_query}' in the current MLRITM faculty database. "
                        f"Please check the official [MLRITM Faculty Profile](https://mlritm.ac.in/faculty-profile) directory."
                    ),
                    intent=intent.value,
                    confidence=route_res.confidence,
                    source="Official MLRITM Faculty Profile System",
                    used_knowledge_base=True,
                    mode=RouterMode.COLLEGE_MODE.value,
                )

            top_f = results[0]
            if focus == "qualifications":
                answer = (
                    f"### 🎓 Educational Qualifications — {top_f['name']}\n"
                    f"*{top_f['designation']}*, {top_f['department']}\n\n"
                    f"- **Undergraduate Degree:** {top_f['undergraduate_degree']}\n"
                    f"- **Postgraduate Degree:** {top_f['postgraduate_degree']}\n"
                    f"- **Ph.D. Degree:** {top_f['phd_degree']}\n"
                    f"- **Area of Specialization:** {top_f['specialization']}\n\n"
                    f"🔗 [View Official Profile]({top_f['profile_url']})"
                )
            elif focus == "specialization":
                answer = (
                    f"### 🔬 Area of Specialization — {top_f['name']}\n"
                    f"*{top_f['designation']}*, {top_f['department']}\n\n"
                    f"- **Area of Specialization:** {top_f['specialization']}\n"
                    f"- **Research Interests:** {top_f['research_interests']}\n\n"
                    f"🔗 [View Official Profile]({top_f['profile_url']})"
                )
            elif focus == "profile_link":
                answer = (
                    f"Here is the official MLRITM faculty profile for **{top_f['name']}** ({top_f['designation']}):\n\n"
                    f"🔗 [View Official Profile]({top_f['profile_url']})"
                )
            elif focus == "experience":
                answer = (
                    f"### ⏳ Experience — {top_f['name']}\n"
                    f"*{top_f['designation']}*, {top_f['department']}\n\n"
                    f"- **Total Experience:** {top_f['total_experience']}\n"
                    f"- **Experience @ MLRITM:** {top_f['experience_mlritm']}\n"
                    f"- **Employment Status:** {top_f['employment_status']}\n\n"
                    f"🔗 [View Official Profile]({top_f['profile_url']})"
                )
            else:
                answer = faculty_service.format_faculty_card(top_f, is_short_query=False)

            return ChatResponse(
                answer=answer,
                intent=intent.value,
                confidence=route_res.confidence,
                source="Official MLRITM Faculty Profile System (https://mlritm.ac.in/faculty-profile)",
                used_knowledge_base=True,
                cards=[top_f],
                mode=RouterMode.COLLEGE_MODE.value,
            )

        # -------------------------------------------------------------
        # 4. Faculty Quantitative Counts
        # -------------------------------------------------------------
        if intent == IntentEnum.FACULTY_COUNT:
            designation_filter = None
            if "professor" in message.lower() and "assistant" not in message.lower() and "associate" not in message.lower():
                designation_filter = "professor"
            elif "assistant" in message.lower():
                designation_filter = "assistant"
            elif "associate" in message.lower():
                designation_filter = "associate"

            count_data = await faculty_service.count_faculty(dept_code, db, designation_filter)
            if dept_code:
                dept_label = dept_name or dept_code
                if designation_filter:
                    cnt = count_data.get(f"{designation_filter}_professors", count_data["total"])
                    ans = f"Based on the currently indexed official MLRITM faculty records, there are **{cnt}** {designation_filter}s listed for **{dept_label}**."
                else:
                    ans = (
                        f"Based on the currently indexed official MLRITM faculty records, there are **{count_data['total']}** faculty members listed for **{dept_label}**.\n\n"
                        f"- **Professors:** {count_data['professors']}\n"
                        f"- **Associate Professors:** {count_data['associate_professors']}\n"
                        f"- **Assistant Professors:** {count_data['assistant_professors']}\n"
                        f"- **Head of Department:** {count_data['hods']}"
                    )
            else:
                ans = f"Based on the currently indexed official MLRITM records, there are **{count_data['total']}** faculty records indexed across all departments."

            ans += "\n\n*Note: Counts are calculated from the verified official MLRITM Faculty Profile system (https://mlritm.ac.in/faculty-profile).*"
            return ChatResponse(
                answer=ans,
                intent=intent.value,
                confidence=route_res.confidence,
                source="Official MLRITM Faculty Profile System",
                used_knowledge_base=True,
                mode=RouterMode.COLLEGE_MODE.value,
            )

        # -------------------------------------------------------------
        # 5. Faculty Subject / Course Search
        # -------------------------------------------------------------
        if intent == IntentEnum.FACULTY_SUBJECT:
            subject = params.get("subject", message)
            matches = await faculty_service.search_faculty(subject, db, department_code=dept_code)
            verified_matches = [f for f in matches if f.get("courses_taught") and f["courses_taught"] != "Not Available" and subject.lower() in f["courses_taught"].lower()]
            if verified_matches:
                lines = [f"Here are the official MLRITM faculty records listed for teaching **{subject}**:"]
                for f in verified_matches:
                    lines.append(f"- **{f['name']}** ({f['designation']}, {f['department']}) — Courses: {f['courses_taught']} [View Profile]({f['profile_url']})")
                ans = "\n".join(lines)
            else:
                ans = "I couldn't verify the current faculty assignment for that subject from the official MLRITM sources."

            return ChatResponse(
                answer=ans,
                intent=intent.value,
                confidence=route_res.confidence,
                source="Official MLRITM Faculty Profile System",
                used_knowledge_base=True,
                mode=RouterMode.COLLEGE_MODE.value,
            )

        # -------------------------------------------------------------
        # 6. Faculty Research, Publications, and Patents
        # -------------------------------------------------------------
        if intent == IntentEnum.FACULTY_RESEARCH:
            focus = params.get("focus")
            target_faculty = params.get("target_faculty")
            research_area = params.get("research_area", message)

            if target_faculty:
                res = await faculty_service.search_faculty(target_faculty, db)
                if res:
                    f = res[0]
                    ans = (
                        f"### 🔬 Research Profile — {f['name']}\n"
                        f"*{f['designation']}*, {f['department']}\n\n"
                        f"- **Area of Specialization:** {f['specialization']}\n"
                        f"- **Research Interests:** {f['research_interests']}\n"
                        f"- **Publications:** {f['publications_summary']}\n"
                        f"- **Patents:** {f['patents_info']}\n"
                    )
                    if f.get("academic_identity_url") and f["academic_identity_url"] != "Not Available":
                        ans += f"- **Academic Identity:** [IRINS Profile]({f['academic_identity_url']})\n"
                    ans += f"\n🔗 [View Official Profile]({f['profile_url']})"
                    return ChatResponse(
                        answer=ans,
                        intent=intent.value,
                        confidence=route_res.confidence,
                        source="Official MLRITM Faculty Profile System",
                        used_knowledge_base=True,
                        mode=RouterMode.COLLEGE_MODE.value,
                    )

            matches = await faculty_service.search_by_research(research_area, db)
            if matches:
                lines = [f"### 🔬 Faculty with Research in {research_area.upper()} ({len(matches)} Faculty Listed)"]
                for i, f in enumerate(matches, 1):
                    lines.append(f"{i}. **{f['name']}** ({f['designation']}, {f['department']})\n   - **Specialization:** {f['specialization']}\n   - **Research:** {f['research_interests']} [View Profile]({f['profile_url']})")
                lines.append("\n*Source: Official MLRITM Faculty Profile System (https://mlritm.ac.in/faculty-profile)*")
                ans = "\n".join(lines)
            else:
                ans = f"No faculty members with officially published research matching '{research_area}' were found in the current indexed records."

            return ChatResponse(
                answer=ans,
                intent=intent.value,
                confidence=route_res.confidence,
                source="Official MLRITM Faculty Profile System",
                used_knowledge_base=True,
                cards=matches[:5],
                mode=RouterMode.COLLEGE_MODE.value,
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
                return await _knowledge_answer(message, intent, route_res.confidence, db)
            return ChatResponse(
                answer=ACCESS_DENIED_ANSWERS[denied.reason],
                intent=intent.value,
                confidence=route_res.confidence,
                source="Student Records Authorization",
                requires_auth=denied.reason == "not_signed_in",
                requires_consent=denied.reason == "consent_required",
                mode=RouterMode.COLLEGE_MODE.value,
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
                mode=RouterMode.COLLEGE_MODE.value,
            )

    # Personal intent, but this route has no database session to authorize against
    if context_engine.requires_personal_data(intent, message) and intent != IntentEnum.FAQ_RAG:
        return ChatResponse(
            answer=ACCESS_DENIED_ANSWERS["not_signed_in"],
            intent=intent.value,
            confidence=route_res.confidence,
            source="Student Records Authorization",
            requires_auth=True,
            mode=RouterMode.COLLEGE_MODE.value,
        )

    # =========================================================================
    # HYBRID MODE: Multi-source queries combining College data + OpenAI / Web
    # E.g. "Tell me about the upcoming AI workshop and who I can contact"
    # =========================================================================
    if intent == IntentEnum.HYBRID_QUERY or (mode == RouterMode.HYBRID_MODE and intent != IntentEnum.PROJECT_IDEAS):
        events = await college_kb.get_upcoming_events(db) if db is not None else []
        faculty = await college_kb.search_faculty(message, db) if db is not None else []
        knowledge_parts = []
        if events:
            knowledge_parts.append("Upcoming Events:\n" + "\n".join([f"- **{e['title']}**: Date: {e['date_display']}, Venue: {e['venue']}, Organizer: {e['organizer']}" for e in events[:3]]))
        if faculty:
            knowledge_parts.append("Faculty Contacts:\n" + "\n".join([f"- **{f['name']}** ({f['designation']}, {f['department']}): Phone: {f['phone']}, Email: {f['email']}" for f in faculty[:2]]))
        college_knowledge = "\n\n".join(knowledge_parts)
        ai_resp = openai_service.generate_response(
            message,
            college_knowledge=college_knowledge,
            history=history,
        )
        final_answer = ai_resp or (college_knowledge if college_knowledge else "Here is the official MLRITM event and contact information.")
        return ChatResponse(
            answer=final_answer,
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Official Sources & OpenAI",
            used_knowledge_base=True,
            mode=RouterMode.HYBRID_MODE.value,
        )

    # =========================================================================
    # PROJECT IDEAS & ENGINEERING GUIDANCE (Hybrid or General AI)
    # =========================================================================
    if intent == IntentEnum.PROJECT_IDEAS:
        search_res = await web_search_service.search(message)
        web_context = web_search_service.format_web_context_block(search_res)
        citations_md = web_search_service.format_citations_markdown(search_res)
        college_context = "MLRITM Engineering Departments: CSE, IT, CS-DS, CS-AIML, ECE, EEE, Mechanical, Civil." if "mlritm" in message.lower() or "college" in message.lower() else None
        ai_resp = openai_service.generate_response(
            message,
            web_context=web_context,
            college_knowledge=college_context,
            history=history,
        )
        final_answer = f"{ai_resp}\n\n{citations_md}" if (ai_resp and citations_md and citations_md not in ai_resp) else (ai_resp or citations_md)
        return ChatResponse(
            answer=final_answer,
            intent=intent.value,
            confidence=route_res.confidence,
            source="OpenAI & Web Intelligence",
            citations=search_res,
            mode=mode.value,
        )

    # =========================================================================
    # GENERAL AI MODE: Coding, Technical Concepts, Current Web Info, General Knowledge
    # =========================================================================
    if mode == RouterMode.GENERAL_AI_MODE:
        if intent == IntentEnum.OUT_OF_SCOPE:
            return ChatResponse(
                answer="I cannot fulfill requests involving system exploits, unauthorized access, or security violations. Please ask an academic, technical, or college-related question.",
                intent=intent.value,
                confidence=route_res.confidence,
                source="Security Guard",
                mode=RouterMode.GENERAL_AI_MODE.value,
            )

        needs_web = (intent == IntentEnum.CURRENT_WEB_INFO) or web_search_service.is_current_info_query(message)
        if needs_web:
            search_res = await web_search_service.search(message)
            web_context = web_search_service.format_web_context_block(search_res)
            citations_md = web_search_service.format_citations_markdown(search_res)
            ai_resp = openai_service.generate_response(
                message,
                web_context=web_context,
                history=history,
            )
            final_answer = f"{ai_resp}\n\n{citations_md}" if (ai_resp and citations_md and citations_md not in ai_resp) else (ai_resp or citations_md)
            return ChatResponse(
                answer=final_answer,
                intent=intent.value,
                confidence=route_res.confidence,
                source="OpenAI & Web Intelligence",
                citations=search_res,
                mode=RouterMode.GENERAL_AI_MODE.value,
            )
        else:
            ai_resp = openai_service.generate_response(message, history=history)
            return ChatResponse(
                answer=ai_resp or f"Here is the explanation regarding '{message}'.",
                intent=intent.value,
                confidence=route_res.confidence,
                source="OpenAI Intelligence",
                mode=RouterMode.GENERAL_AI_MODE.value,
            )

    # =========================================================================
    # FACULTY & PHONE DIRECTORY (Officials, HODs, Phone Numbers, Emails)
    # =========================================================================
    if intent in [IntentEnum.PHONE_DIRECTORY, IntentEnum.FACULTY_DIRECTORY]:
        results = await college_kb.search_faculty(message, db) if db is not None else []
        if not results:
            return ChatResponse(
                answer="I couldn't find an official contact matching that request in the official MLRITM Phone Directory. Please check [MLRITM Phone Directory](https://www.mlritm.ac.in/Phone_Directory) or contact the Academic Section (info@mlritm.ac.in).",
                intent=intent.value,
                confidence=route_res.confidence,
                source="MLRITM Official Phone Directory",
                used_knowledge_base=True,
            )
        cards = []
        md_blocks = []
        for c in results:
            cards.append({
                "type": "faculty_contact",
                "name": c["name"],
                "designation": c["designation"],
                "department": c["department"],
                "responsibility": c["responsibility"],
                "phone": c["phone"],
                "email": c["email"],
                "source": c["source"],
            })
            phone_link = f"[{c['phone']}](tel:{c['phone'].replace(' ', '')})" if c['phone'] != 'Not Available' else 'Not Available'
            email_link = f"[{c['email']}](mailto:{c['email']})" if c['email'] != 'Not Available' else 'Not Available'
            block = (
                f"### 👤 {c['name']}\n"
                f"- **Designation:** {c['designation']}\n"
                f"- **Department / Responsibility:** {c['department']}\n"
                f"- **Phone:** 📞 {phone_link}\n"
                f"- **Email:** ✉️ {email_link}\n"
                f"- **Official Source:** [MLRITM Phone Directory]({c['source']})"
            )
            md_blocks.append(block)

        answer_text = "Here is the official contact information from the MLRITM Phone Directory:\n\n" + "\n\n---\n\n".join(md_blocks)
        return ChatResponse(
            answer=answer_text,
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Official Phone Directory",
            cards=cards,
            used_knowledge_base=True,
        )

    # =========================================================================
    # UPCOMING EVENTS
    # =========================================================================
    if intent == IntentEnum.EVENTS_UPCOMING:
        events = await college_kb.get_upcoming_events(db) if db is not None else []
        if not events:
            return ChatResponse(
                answer="No upcoming event information was found in the official MLRITM sources at this time. Please check the official [MLRITM Website](https://www.mlritm.ac.in/upcoming-and-ongoing-events) for new announcements.",
                intent=intent.value,
                confidence=route_res.confidence,
                source="MLRITM Events Portal",
                used_knowledge_base=True,
            )
        cards = []
        md_blocks = []
        for ev in events:
            cards.append({
                "type": "event",
                "title": ev["title"],
                "date": ev["date_display"],
                "venue": ev["venue"],
                "organizer": ev["organizer"],
                "description": ev["description"],
                "poster_url": ev.get("poster_url"),
                "source": ev["source"],
            })
            poster_md = f"\n![{ev['title']}]({ev['poster_url']})\n" if ev.get("poster_url") else ""
            block = (
                f"### 📅 {ev['title']}\n"
                f"- **Date:** {ev['date_display']}\n"
                f"- **Venue:** {ev['venue']}\n"
                f"- **Organizer:** {ev['organizer']}\n"
                f"- **Description:** {ev['description']}\n"
                f"{poster_md}"
                f"- **Official Source:** [MLRITM Official Website]({ev['source']})"
            )
            md_blocks.append(block)

        answer_text = "Here are the upcoming official MLRITM events:\n\n" + "\n\n---\n\n".join(md_blocks)
        return ChatResponse(
            answer=answer_text,
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Events Portal",
            cards=cards,
            used_knowledge_base=True,
        )

    # =========================================================================
    # PAST EVENTS & HISTORICAL ACTIVITIES
    # =========================================================================
    if intent == IntentEnum.EVENTS_PAST:
        events = await college_kb.get_past_events(message, db) if db is not None else []
        if not events:
            return ChatResponse(
                answer="No historical event information matching that period was found in the indexed official MLRITM sources.",
                intent=intent.value,
                confidence=route_res.confidence,
                source="MLRITM Events Portal",
                used_knowledge_base=True,
            )
        cards = []
        md_blocks = []
        for ev in events:
            cards.append({
                "type": "event",
                "title": ev["title"],
                "date": ev["date_display"],
                "venue": ev["venue"],
                "organizer": ev["organizer"],
                "description": ev["description"],
                "poster_url": ev.get("poster_url"),
                "source": ev["source"],
            })
            block = (
                f"### 🏛️ {ev['title']}\n"
                f"- **Date:** {ev['date_display']}\n"
                f"- **Venue:** {ev['venue']}\n"
                f"- **Organizer:** {ev['organizer']}\n"
                f"- **Details:** {ev['description']}\n"
                f"- **Official Source:** [MLRITM Official Website]({ev['source']})"
            )
            md_blocks.append(block)

        answer_text = "Here are the previous official MLRITM events from our historical archives:\n\n" + "\n\n---\n\n".join(md_blocks)
        return ChatResponse(
            answer=answer_text,
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Events Portal",
            cards=cards,
            used_knowledge_base=True,
        )

    # =========================================================================
    # OFFICIAL EVENT PHOTOS & MEDIA GALLERY
    # =========================================================================
    if intent == IntentEnum.EVENT_PHOTOS:
        # If this is a conversational follow-up ("show photos", "photos of that"), resolve query from history
        photo_query = message
        if route_res.parameters.get("followup") or re.search(r"\b(that|it|the event|this event)\b", message.lower()):
            if history:
                for turn in reversed(history):
                    c = turn.get("content", "").lower()
                    for ev_name in ["hackathon", "valarous", "valorous", "ranakrida", "sangram", "ai workshop", "pace"]:
                        if ev_name in c:
                            photo_query = ev_name
                            break
                    if photo_query != message:
                        break
        photo_res = await college_kb.get_event_photos(photo_query, db) if db is not None else {}
        if not photo_res.get("found"):
            return ChatResponse(
                answer="No official photos were found for this event.",
                intent=intent.value,
                confidence=route_res.confidence,
                source="MLRITM Media & Gallery",
                used_knowledge_base=True,
                mode=RouterMode.COLLEGE_MODE.value,
            )
        gallery_md = []
        for p in photo_res["photos"]:
            gallery_md.append(f"![{photo_res['event_name']}]({p})")

        photos_text = "\n".join(gallery_md)
        answer_text = (
            f"### 📸 Official Photos: {photo_res['event_name']}\n"
            f"- **Date:** {photo_res['date_display']}\n"
            f"- **Venue:** {photo_res['venue']}\n\n"
            f"{photos_text}\n\n"
            f"- **Official Source:** [MLRITM Media Gallery]({photo_res['source']})"
        )
        return ChatResponse(
            answer=answer_text,
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Media & Gallery",
            cards=[{
                "type": "photo_gallery",
                "event_name": photo_res["event_name"],
                "photos": photo_res["photos"],
                "source": photo_res["source"],
            }],
            used_knowledge_base=True,
            mode=RouterMode.COLLEGE_MODE.value,
        )

    # =========================================================================
    # EVENT DETAILS
    # =========================================================================
    if intent == IntentEnum.EVENT_DETAILS:
        # Conversational follow-up: filter keyword (e.g. "Which one is related to AI?")
        if route_res.parameters.get("is_followup_event_filter"):
            filter_kw = route_res.parameters.get("filter_keyword", "ai").lower()
            all_events = (await college_kb.get_upcoming_events(db) if db is not None else []) + (await college_kb.get_past_events(message, db) if db is not None else [])
            matched = [e for e in all_events if filter_kw in e['title'].lower() or filter_kw in e['description'].lower()]
            if matched:
                ev = matched[0]
                return ChatResponse(
                    answer=(
                        f"### ℹ️ {ev['title']}\n"
                        f"- **Date:** {ev['date_display']}\n"
                        f"- **Venue:** {ev['venue']}\n"
                        f"- **Organizer:** {ev['organizer']}\n"
                        f"- **Overview:** {ev['description']}\n"
                        f"- **Source:** [MLRITM Official Website]({ev['source']})"
                    ),
                    intent=intent.value,
                    confidence=route_res.confidence,
                    source="MLRITM Events Portal",
                    used_knowledge_base=True,
                    mode=RouterMode.COLLEGE_MODE.value,
                )

        # Conversational follow-up: date enquiry (e.g. "When is it?")
        if route_res.parameters.get("is_followup_event_date"):
            hist_str = str(history).lower() if history else ""
            upcoming = await college_kb.get_upcoming_events(db) if db is not None else []
            target = None
            for e in upcoming:
                if any(w in e['title'].lower() for w in ["ai", "hackathon", "workshop", "fest"]) and any(w in hist_str for w in ["ai", "hackathon", "workshop", "fest"]):
                    target = e
                    break
            if not target and upcoming:
                target = upcoming[0]
            if target:
                return ChatResponse(
                    answer=f"The **{target['title']}** is scheduled for **{target['date_display']}** at {target['venue']}.",
                    intent=intent.value,
                    confidence=route_res.confidence,
                    source="MLRITM Events Portal",
                    used_knowledge_base=True,
                    mode=RouterMode.COLLEGE_MODE.value,
                )

        events = await college_kb.get_past_events(message, db, limit=1) if db is not None else []
        if not events and db is not None:
            events = await college_kb.get_upcoming_events(db, limit=1)
        if events:
            ev = events[0]
            answer_text = (
                f"### ℹ️ {ev['title']}\n"
                f"- **Date:** {ev['date_display']}\n"
                f"- **Venue:** {ev['venue']}\n"
                f"- **Organizer:** {ev['organizer']}\n"
                f"- **Overview:** {ev['description']}\n"
                f"- **Source:** [MLRITM Official Website]({ev['source']})"
            )
            return ChatResponse(
                answer=answer_text,
                intent=intent.value,
                confidence=route_res.confidence,
                source="MLRITM Events Portal",
                used_knowledge_base=True,
                mode=RouterMode.COLLEGE_MODE.value,
            )

    # =========================================================================
    # COURSES & DEPARTMENTS AVAILABLE
    # =========================================================================
    if intent == IntentEnum.COURSES:
        return ChatResponse(
            answer=(
                "### 🎓 Academic Departments & B.Tech Programs at MLRITM (Autonomous):\n\n"
                "1. **Computer Science and Engineering (CSE)**\n"
                "2. **CSE - Data Science (CSE-DS)**\n"
                "3. **CSE - Artificial Intelligence & Machine Learning (CSE-AI&ML)**\n"
                "4. **CSE - Cyber Security (CSE-CS)**\n"
                "5. **Information Technology (IT) & Computer Science and Information Technology (CSIT)**\n"
                "6. **Electronics and Communication Engineering (ECE)**\n"
                "7. **Electrical and Electronics Engineering (EEE)**\n"
                "8. **Mechanical Engineering (ME)**\n"
                "9. **Civil Engineering (CE)**\n"
                "10. **Master of Business Administration (MBA)**\n"
                "11. **Freshman Engineering (Humanities & Sciences)**\n\n"
                "**EAMCET Code:** `MLRS` | **Affiliation:** JNTU Hyderabad | **Accreditation:** NAAC & NBA\n\n"
                "Source: [MLRITM Official Departments](https://www.mlritm.ac.in/Departments)"
            ),
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Official Website",
            used_knowledge_base=True,
        )

    # =========================================================================
    # ADMISSIONS
    # =========================================================================
    if intent == IntentEnum.ADMISSIONS:
        return ChatResponse(
            answer=(
                "### 🏛️ MLRITM B.Tech Admissions Details:\n\n"
                "- **EAMCET Counseling Code:** `MLRS`\n"
                "- **Convener Quota (Category-A):** 70% of total intake filled through TS EAPCET / EAMCET centralized web counseling.\n"
                "- **Management Quota (Category-B):** 30% of seats allocated based on merit / JEE Main / NRI guidelines.\n"
                "- **Lateral Entry:** Diploma holders admitted directly into 2nd year (3rd semester) via TS ECET (10% supernumerary seats).\n"
                "- **Admission Reporting:** Candidates must report with Allotment Order, Joining Report, and original certificates at the MLRITM Administrative Block.\n\n"
                "Source: [MLRITM Admissions Portal](https://www.mlritm.ac.in/study-with-us)"
            ),
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Admissions",
            used_knowledge_base=True,
        )

    # =========================================================================
    # PLACEMENTS
    # =========================================================================
    if intent == IntentEnum.PLACEMENTS:
        return ChatResponse(
            answer=(
                "### 💼 MLRITM Campus Placements Overview:\n\n"
                "- **Placement Cell Head:** Mr. B N Srinivas (Placement Officer / TPO)\n"
                "- **Contact:** 📞 [9849872510](tel:9849872510) | ✉️ [tpo@mlritm.ac.in](mailto:tpo@mlritm.ac.in)\n\n"
                "**Top Recruiters & Select Placement Offers (2026 Batch):**\n"
                "- Cognizant: 72 offers\n"
                "- TVS: 51 offers\n"
                "- TCS: 30 offers\n"
                "- Infosys: 27 offers\n"
                "- HCL: 21 offers\n"
                "- ITC Infotech: 13 offers\n"
                "- Integer Telecom: 9 offers\n"
                "- Deloitte: 7 offers\n"
                "- Movate: 7 offers\n"
                "- Juspay, Aveva, Work4Flow, GE Appliances, Virtusa\n\n"
                "Source: [MLRITM Training & Placement Cell](https://www.mlritm.ac.in/placement_cell)"
            ),
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Placements",
            used_knowledge_base=True,
        )

    # =========================================================================
    # SCHOLARSHIPS & FINANCIAL ASSISTANCE
    # =========================================================================
    if intent == IntentEnum.SCHOLARSHIP:
        return ChatResponse(
            answer=(
                "### 🎖️ MLRITM Scholarships & Fee Reimbursement:\n\n"
                "- **TS ePASS Fee Reimbursement:** Full/partial tuition fee reimbursement by Government of Telangana for eligible SC, ST, BC, EBC, and Minority students.\n"
                "- **Scholarships In-Charge:** Mr. B Siva Bala Prasad\n"
                "- **Contact:** 📞 [9866383999](tel:9866383999) | ✉️ [scholarship@mlritm.ac.in](mailto:scholarship@mlritm.ac.in)\n"
                "- **Office:** Scholarship Section, Administrative Block, MLRITM\n"
                "- **Eligibility:** Valid income certificate and TS EAMCET allotment order under convener quota.\n\n"
                "Source: [MLRITM Scholarships](https://www.mlritm.ac.in/Phone_Directory)"
            ),
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Scholarships",
            used_knowledge_base=True,
        )

    # =========================================================================
    # NOTICES & CIRCULARS
    # =========================================================================
    if intent == IntentEnum.NOTICES:
        return ChatResponse(
            answer=(
                "### 📢 Recent MLRITM Official Notices & Circulars:\n\n"
                "- **Sri Krishna Ashtami Holiday Circular:** Declared holiday on 03.09.2026 for all B.Tech & MBA classes.\n"
                "- **Eid Miladun Nabi Holiday Circular:** Circular issued on 25.08.2026.\n"
                "- **Campus Placement 2026 Drives:** Ongoing interviews with Cognizant, TVS, TCS, Infosys, and HCL.\n"
                "- **B.Tech Autonomous Academic Calendars (2025-26):** Published for Semesters I through VIII.\n\n"
                "Source: [MLRITM Official Announcements](https://www.mlritm.ac.in/)"
            ),
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Official Website",
            used_knowledge_base=True,
        )

    # =========================================================================
    # ACADEMIC CALENDAR & HOLIDAYS (institution-wide, no sign-in needed)
    # =========================================================================
    if intent == IntentEnum.ACADEMIC_CALENDAR:
        return ChatResponse(
            answer=(
                "### 📅 MLRITM Academic Calendars (2025-26):\n\n"
                "Official Autonomous Semester Calendars approved by the Academic Council:\n"
                "- [B.Tech I & II Semester Academic Calendar 2025-26](https://www.mlritm.ac.in/Academic-Calendar/25-26/B.Tech-I-II-Semester-Academic-Calendar-2025-26.pdf)\n"
                "- [B.Tech III & IV Semester Academic Calendar 2025-26](https://www.mlritm.ac.in/Academic-Calendar/25-26/B.Tech-III-IV-Semester-Academic-Calendar-2025-26.pdf)\n"
                "- [B.Tech V & VI Semester Academic Calendar 2025-26](https://www.mlritm.ac.in/Academic-Calendar/25-26/B.Tech-V-and-VI-Semester-Academic-Calendar-2025-26.pdf)\n"
                "- [B.Tech VII & VIII Semester Academic Calendar 2025-26](https://www.mlritm.ac.in/Academic-Calendar/25-26/B.Tech-VII-and-VIII-Semester-Academic-Calendar-2025-26.pdf)\n"
                "- [M.Tech I & II Semester Academic Calendar 2025-26](https://www.mlritm.ac.in/Academic-Calendar/25-26/M.Tech-I-II-Semester-Academic-Calendar-2025-26.pdf)\n\n"
                "Source: [MLRITM Academic Calendar](https://www.mlritm.ac.in/academic_calendar)"
            ),
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Academic Calendar",
            used_knowledge_base=True,
        )

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
    # GENERAL COLLEGE INFORMATION & ADAPTIVE KNOWLEDGE RESOLUTION
    # =========================================================================
    if intent == IntentEnum.GENERAL_COLLEGE:
        # If the question is a specific or unindexed query, route through adaptive knowledge
        is_generic_college_info = any(
            k in message.lower() for k in [
                "address", "where is", "location", "contact email", "college phone",
                "office hours", "about mlritm", "who founded", "eamcet code", "autonomous status",
                "accreditation", "affiliat"
            ]
        )
        if not is_generic_college_info and db is not None:
            adaptive_res = await adaptive_knowledge_service.process_query(message, db)
            return ChatResponse(
                answer=adaptive_res["answer"],
                intent="ADAPTIVE_KNOWLEDGE",
                confidence=adaptive_res["confidence"],
                source=adaptive_res["source"],
                citations=adaptive_res.get("citations"),
                used_knowledge_base=adaptive_res["is_verified"],
            )

        return ChatResponse(
            answer=(
                "### 🏛️ Marri Laxman Reddy Institute of Technology and Management (MLRITM)\n\n"
                "- **Status:** Autonomous Institution approved by AICTE, New Delhi and affiliated to JNTUH.\n"
                "- **Accreditation:** NAAC Accredited with 'A' Grade, NBA Accredited.\n"
                "- **Address:** Dundigal, Medchal - Malkajgiri, Hyderabad, Telangana - 500043, India.\n"
                "- **Phone:** 📞 [040-29556182](tel:040-29556182)\n"
                "- **Email:** ✉️ [info@mlritm.ac.in](mailto:info@mlritm.ac.in)\n"
                "- **EAMCET Code:** `MLRS`\n"
                "- **Office Hours:** Monday to Saturday: 9:00 AM - 5:00 PM\n\n"
                "Source: [MLRITM Official Website](https://www.mlritm.ac.in/)"
            ),
            intent=intent.value,
            confidence=route_res.confidence,
            source="MLRITM Official Information",
            used_knowledge_base=True,
        )

    # =========================================================================
    # STATIC KNOWLEDGE (RAG over the official regulations)
    # =========================================================================
    if intent == IntentEnum.FAQ_RAG:
        return await _knowledge_answer(message, intent, route_res.confidence, db)

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
    # OPEN-ENDED OUT-OF-BOX RESOLUTION (OpenAI + Web Search)
    # Never reject unexpected or general questions with "I don't know" or "Outside my scope".
    # Instead, search authorized web sources if helpful and synthesize a grounded answer.
    # =========================================================================
    if intent == IntentEnum.OUT_OF_SCOPE:
        return ChatResponse(
            answer="I cannot fulfill requests involving system exploits, unauthorized access, or security violations. Please ask an academic, technical, or college-related question.",
            intent=intent.value,
            confidence=route_res.confidence,
            source="Security Guard",
            mode=RouterMode.GENERAL_AI_MODE.value,
        )

    needs_web = web_search_service.is_current_info_query(message)
    search_res = await web_search_service.search(message) if needs_web else []
    web_ctx = web_search_service.format_web_context_block(search_res) if search_res else None
    citations_md = web_search_service.format_citations_markdown(search_res) if search_res else None
    ai_ans = openai_service.generate_response(message, web_context=web_ctx, history=history)
    final_ans = f"{ai_ans}\n\n{citations_md}" if (ai_ans and citations_md and citations_md not in ai_ans) else (ai_ans or citations_md or "Here is the information regarding your question.")
    return ChatResponse(
        answer=final_ans,
        intent=intent.value if hasattr(intent, "value") else str(intent),
        confidence=route_res.confidence,
        source="OpenAI & Web Intelligence" if search_res else "OpenAI Intelligence",
        citations=search_res if search_res else None,
        mode=RouterMode.GENERAL_AI_MODE.value,
    )


async def _knowledge_answer(
    message: str,
    intent: IntentEnum,
    confidence: float,
    db: Optional[AsyncSession] = None,
) -> ChatResponse:
    """A purely institutional answer from the official knowledge base or adaptive learning pipeline."""
    rag_res = rag_engine.answer_query(message)
    if not rag_res.get("is_fallback", False):
        return ChatResponse(
            answer=rag_res["answer"],
            intent=intent.value,
            confidence=confidence,
            source="MLRITM Autonomous Academic Regulations",
            citations=rag_res.get("citations"),
            used_knowledge_base=True,
        )

    # RAG didn't find high-confidence static match: invoke adaptive knowledge system
    if db is not None:
        adaptive_res = await adaptive_knowledge_service.process_query(message, db)
        return ChatResponse(
            answer=adaptive_res["answer"],
            intent="ADAPTIVE_KNOWLEDGE",
            confidence=adaptive_res["confidence"],
            source=adaptive_res["source"],
            citations=adaptive_res.get("citations"),
            used_knowledge_base=adaptive_res["is_verified"],
        )

    return ChatResponse(
        answer=rag_res["answer"],
        intent=intent.value,
        confidence=confidence,
        source="MLRITM Autonomous Academic Regulations",
        citations=rag_res.get("citations"),
        used_knowledge_base=False,
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
    return await process_chat_query(req.message, student, db, history=req.conversation_history)


@router.post("/stream")
async def chat_stream_endpoint(
    req: ChatRequest,
    student: Optional[StudentContext] = Depends(get_student_context),
    db: AsyncSession = Depends(get_db),
):
    """Server-Sent Events (SSE) Streaming Endpoint."""

    async def event_generator():
        res = await process_chat_query(req.message, student, db, history=req.conversation_history)
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
                "cards": res.cards,
            })
            yield f"data: {data}\n\n"
            await asyncio.sleep(0.03)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class FeedbackRequest(BaseModel):
    query: str
    feedback: str  # "HELPFUL" or "NOT_HELPFUL"
    missing_info: Optional[str] = None
    session_id: Optional[str] = None


@router.post("/feedback")
async def chat_feedback_endpoint(
    req: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    User Feedback Loop (Helpful / Not Helpful).
    Collects student signals on missing or incomplete information without treating feedback as factual truth.
    """
    fb = await adaptive_knowledge_service.record_feedback(
        query=req.query,
        feedback_type=req.feedback,
        missing_info=req.missing_info,
        db=db,
        session_id=req.session_id,
    )
    return {
        "status": "success",
        "feedback_id": fb.id,
        "feedback_type": fb.feedback_type,
        "message": "Thank you for your feedback! It helps improve our college knowledge base.",
    }
