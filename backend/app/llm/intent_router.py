"""
Intent Router for Academic Advising System.
Routes student queries to one of 14 intents:
- FAQ_RAG: Academic regulations, syllabus, fees, scholarships
- ATTENDANCE: Attendance percentage, subject-wise shortage, recovery targets
- RESULTS: Semester SGPA, CGPA, grades, backlogs
- MARKS: Internal / mid-term / assignment marks for the current semester
- TIMETABLE: Class schedules, room numbers, lecture timings
- SUBJECTS: The subjects the student is enrolled in this semester
- FACULTY: Who teaches the student a given subject
- EXAMS: Examination schedule, hall tickets
- ASSIGNMENTS: Assignment titles, due dates, submission status
- PROFILE: The student's own academic profile summary
- HOLIDAYS: Academic calendar, upcoming holidays, working days
- SMALL_TALK: Greetings and conversational pleasantries
- OUT_OF_SCOPE: Non-academic queries, off-topic requests
- ESCALATE: Grievances, disciplinary issues, emergency contacts

The router sees only the question text. It never receives an identity and never decides who may
read what: it picks a category, and the backend then loads that category for the signed-in
student only (see services/authorization.py).

Provides GBNF-constrained Llama 2 generation with a deterministic fast-path fallback.
"""

import re
import json
import logging
from enum import Enum
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class IntentEnum(str, Enum):
    FAQ_RAG = "FAQ_RAG"
    ATTENDANCE = "ATTENDANCE"
    RESULTS = "RESULTS"
    MARKS = "MARKS"
    TIMETABLE = "TIMETABLE"
    SUBJECTS = "SUBJECTS"
    FACULTY = "FACULTY"
    EXAMS = "EXAMS"
    ASSIGNMENTS = "ASSIGNMENTS"
    PROFILE = "PROFILE"
    HOLIDAYS = "HOLIDAYS"
    SMALL_TALK = "SMALL_TALK"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ESCALATE = "ESCALATE"


class IntentResult(BaseModel):
    intent: IntentEnum
    confidence: float = Field(ge=0.0, le=1.0)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    routed_by: str = "heuristic_fast_path"  # or 'llama_gbnf'


class IntentRouter:
    def __init__(self, llm_engine=None, gbnf_grammar_path: Optional[str] = None):
        self.llm_engine = llm_engine
        self.gbnf_grammar = None
        if gbnf_grammar_path:
            try:
                with open(gbnf_grammar_path, "r", encoding="utf-8") as f:
                    self.gbnf_grammar = f.read()
            except Exception as e:
                logger.warning(f"Could not load GBNF grammar from {gbnf_grammar_path}: {e}")

    def route_fast_path(self, query: str) -> Optional[IntentResult]:
        """High-precision, deterministic regex patterns for zero-latency routing."""
        q = query.lower().strip()

        # 1. Escalation / Grievances / Distress / Anti-Ragging
        if re.search(r"\b(ragging|harassment|suicide|emergency|distress|complaint against|police|grievance|depression|counselor|squad)\b", q):
            return IntentResult(intent=IntentEnum.ESCALATE, confidence=1.0, parameters={"type": "grievance_or_emergency"})

        # 2. Out of Scope (Non-academic / Off-topic / Abuse)
        if re.search(r"\b(mbbs|bds|medical|cricket|movie|recipe|hack|password|bitcoin|crypto|poem)\b", q):
            return IntentResult(intent=IntentEnum.OUT_OF_SCOPE, confidence=0.96, parameters={})

        # 3. Small Talk / Greetings
        if re.search(r"\b(hi|hello|hey|good (morning|afternoon|evening)|how are you|who are you|thanks?|thank you)\b", q) and not re.search(r"\b(fee|exam|attendance|result|schedule|syllabus)\b", q):
            return IntentResult(intent=IntentEnum.SMALL_TALK, confidence=0.99, parameters={})

        # 4. Holidays / Academic Calendar / Exam Dates
        if re.search(r"\b(holiday|is tomorrow a holiday|vacations?|working days? left|semester end exams? date|exam dates?|calendar|upcoming holidays)\b", q):
            return IntentResult(intent=IntentEnum.HOLIDAYS, confidence=0.95, parameters={"metric": "calendar"})

        # 5. Academic Regulations Policy Check (FAQ_RAG) - prior to live student queries
        if re.search(r"\b(can i get condonation|condonation if|condonation allowed|detention rules?|promotion criteria|credits? needed|eligib|tuition fee|fee for|fee structure|scholarship|epass|regulation|grading system)\b", q):
            return IntentResult(intent=IntentEnum.FAQ_RAG, confidence=0.92, parameters={"topic": "academic_regulations"})

        # 6. Attendance (percentage, subject-wise shortage, recovery)
        if re.search(r"\b(attendance|am i detained|classes (do i need|needed)|reach 75%|which subjects? (have|has) (low|poor|bad|short))\b", q):
            return IntentResult(intent=IntentEnum.ATTENDANCE, confidence=0.98, parameters={"metric": "attendance"})

        # 7. Internal / mid-term marks (checked before RESULTS, which owns SGPA and grades)
        if re.search(r"\b(internal marks?|internals|mid.?term marks?|mid.?[12]\s+marks?|marks? in mid|sessional marks?|assignment marks?|my marks?|cie marks?)\b", q):
            return IntentResult(intent=IntentEnum.MARKS, confidence=0.96, parameters={"metric": "internal_marks"})

        # 8. Semester examination results
        # "my results", but also "my latest results" / "my last semester results"
        if re.search(r"\b(my(\s+\w+){0,2}\s+results?|results?\b.*\bthis semester|sgpa|cgpa|grades? in|my grades?|did i pass|semester marks?|backlogs?)\b", q):
            return IntentResult(intent=IntentEnum.RESULTS, confidence=0.98, parameters={"metric": "results"})

        # 9. Examination schedule (before TIMETABLE, whose "schedule" also matches "exam schedule")
        if re.search(r"\b(exam schedule|examination schedule|next exam|upcoming exams?|my exams?|hall ticket|exam time ?table|when (is|are) (my|the) exams?|mid.?[12]\b)\b", q):
            return IntentResult(intent=IntentEnum.EXAMS, confidence=0.95, parameters={"metric": "exams"})

        # 10. Class timetable (before FACULTY, which would otherwise claim "who is taking ...")
        if re.search(r"\b(timetable|schedule|next class|which room|lab start|next lecture|period timing|class today|classes today|classes tomorrow)\b", q):
            return IntentResult(intent=IntentEnum.TIMETABLE, confidence=0.95, parameters={"metric": "timetable"})

        # 11. Faculty for a subject
        if re.search(r"\b(faculty|who teaches|who is teaching|who handles|my teacher|my professor|class teacher|subject teacher|lecturer for|professor for|hod of)\b", q):
            return IntentResult(intent=IntentEnum.FACULTY, confidence=0.95, parameters={"metric": "faculty"})

        # 12. Enrolled subjects this semester
        if re.search(r"\b(my subjects?|which subjects?|what subjects?|subjects? (am i|i am) (studying|taking|doing|learning)|my courses?|subjects? this semester|current semester subjects?|my curriculum|subject list)\b", q):
            return IntentResult(intent=IntentEnum.SUBJECTS, confidence=0.95, parameters={"metric": "subjects"})

        # 13. Assignments
        if re.search(r"\b(assignments?|submissions?|homework|due date|pending work)\b", q):
            return IntentResult(intent=IntentEnum.ASSIGNMENTS, confidence=0.94, parameters={"metric": "assignments"})

        # 14. Academic profile summary
        if re.search(r"\b((my\s+)?profile(\s+data)?|academic profile|my details|student profile|profile details|my department|my branch|my section|my roll number|which semester am i|what semester am i|my academic (year|info|details)|who am i)\b", q):
            return IntentResult(intent=IntentEnum.PROFILE, confidence=0.95, parameters={"metric": "profile"})

        return None

    def route(self, query: str) -> IntentResult:
        """Routes student query using fast-path or GBNF grammar-constrained LLM."""
        # Check high-confidence fast path first (0 ms latency)
        fast_result = self.route_fast_path(query)
        if fast_result and fast_result.confidence >= 0.90:
            return fast_result

        # If LLM with GBNF is available, query with strict grammar
        if self.llm_engine and self.gbnf_grammar:
            try:
                system_prompt = (
                    "Classify the student query into one of: FAQ_RAG, ATTENDANCE, RESULTS, MARKS, "
                    "TIMETABLE, SUBJECTS, FACULTY, EXAMS, ASSIGNMENTS, PROFILE, HOLIDAYS, "
                    "SMALL_TALK, OUT_OF_SCOPE, ESCALATE. "
                    "Respond ONLY with valid JSON. Never include names, roll numbers or identifiers."
                )
                prompt = f"<s>[INST] <<SYS>>\n{system_prompt}\n<</SYS>>\n\nQuery: {query} [/INST] "
                raw_output = self.llm_engine(prompt, grammar=self.gbnf_grammar, max_tokens=128)
                parsed = json.loads(raw_output)
                return IntentResult(
                    intent=IntentEnum(parsed["intent"]),
                    confidence=float(parsed.get("confidence", 0.85)),
                    parameters=parsed.get("parameters", {}),
                    routed_by="llama_gbnf"
                )
            except Exception as e:
                logger.warning(f"GBNF LLM routing failed ({e}); defaulting to FAQ_RAG.")

        # Conservative default
        return IntentResult(intent=IntentEnum.FAQ_RAG, confidence=0.70, parameters={"query": query})


if __name__ == "__main__":
    router = IntentRouter()
    test_queries = [
        "What is my attendance percentage?",
        "How much is the tuition fee for B.Tech CSE?",
        "Is tomorrow a holiday?",
        "Can I study MBBS here?",
        "Someone is ragging me near the library",
        "Hello!"
    ]
    for q in test_queries:
        res = router.route(q)
        print(f"[{res.intent.value:12s}] (conf: {res.confidence:.2f}) <- '{q}'")
