"""
Intent Router & Query Analyzer for MLRITM Student AI Assistant.
Routes student queries with high precision to:
1. COLLEGE_MODE:
   - Personal Student Records (ATTENDANCE, RESULTS, MARKS, TIMETABLE, SUBJECTS, PROFILE, EXAMS, ASSIGNMENTS)
   - Official College Knowledge (EVENTS_UPCOMING, EVENTS_PAST, EVENT_DETAILS, EVENT_PHOTOS,
     PHONE_DIRECTORY, ADMISSIONS, COURSES, NOTICES, PLACEMENTS, SCHOLARSHIP, ACADEMIC_CALENDAR,
     HOLIDAYS, GENERAL_COLLEGE, FAQ_RAG)
2. GENERAL_AI_MODE:
   - General Knowledge (GENERAL_KNOWLEDGE, e.g. "What is quantum computing?", "Explain recursion")
   - Coding & Tech Concepts (CODING_TECH, e.g. "Explain Python decorators", "How does Docker work?")
   - Current Web Information (CURRENT_WEB_INFO, e.g. "What is the latest version of Python?", "Latest AI news")
   - Project Ideas & Guidance (PROJECT_IDEAS, e.g. "Give me a Python project idea")
3. HYBRID_MODE:
   - Multi-source queries combining college context and general web intelligence
     (e.g., "Tell me about the upcoming AI workshop and who I can contact", "MLRITM capstone project ideas")
4. SAFETY & CONVERSATIONAL:
   - ESCALATE: Distress, grievance, anti-ragging, emergency
   - SMALL_TALK: Greetings & conversational pleasantries
   - OUT_OF_SCOPE: Malicious hacking, security exploits, credential harvesting
"""

import re
import json
import logging
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RouterMode(str, Enum):
    COLLEGE_MODE = "COLLEGE_MODE"
    GENERAL_AI_MODE = "GENERAL_AI_MODE"
    HYBRID_MODE = "HYBRID_MODE"


class IntentEnum(str, Enum):
    # Personal Student Intents (COLLEGE_MODE)
    ATTENDANCE = "ATTENDANCE"
    RESULTS = "RESULTS"
    MARKS = "MARKS"
    TIMETABLE = "TIMETABLE"
    SUBJECTS = "SUBJECTS"
    FACULTY = "FACULTY"  # personal teacher for a course
    EXAMS = "EXAMS"
    ASSIGNMENTS = "ASSIGNMENTS"
    PROFILE = "PROFILE"
    STUDENT_PROFILE = "STUDENT_PROFILE"
    STUDENT_ATTENDANCE = "STUDENT_ATTENDANCE"
    STUDENT_MARKS = "STUDENT_MARKS"
    STUDENT_RESULTS = "STUDENT_RESULTS"
    STUDENT_TIMETABLE = "STUDENT_TIMETABLE"

    # Official College Knowledge Intents (COLLEGE_MODE)
    EVENTS_UPCOMING = "EVENTS_UPCOMING"
    EVENTS_PAST = "EVENTS_PAST"
    EVENT_DETAILS = "EVENT_DETAILS"
    EVENT_PHOTOS = "EVENT_PHOTOS"
    PHONE_DIRECTORY = "PHONE_DIRECTORY"
    FACULTY_DIRECTORY = "FACULTY_DIRECTORY"
    ADMISSIONS = "ADMISSIONS"
    COURSES = "COURSES"
    NOTICES = "NOTICES"
    PLACEMENTS = "PLACEMENTS"
    SCHOLARSHIP = "SCHOLARSHIP"
    ACADEMIC = "ACADEMIC"
    ACADEMIC_CALENDAR = "ACADEMIC_CALENDAR"
    HOLIDAYS = "HOLIDAYS"
    GENERAL_COLLEGE = "GENERAL_COLLEGE"
    FAQ_RAG = "FAQ_RAG"

    # Open-Ended & Web Intelligence Intents (GENERAL_AI_MODE / HYBRID_MODE)
    GENERAL_KNOWLEDGE = "GENERAL_KNOWLEDGE"
    CODING_TECH = "CODING_TECH"
    CURRENT_WEB_INFO = "CURRENT_WEB_INFO"
    PROJECT_IDEAS = "PROJECT_IDEAS"
    HYBRID_QUERY = "HYBRID_QUERY"

    # Faculty Intelligence & Short Query Platform Intents (COLLEGE_MODE)
    FACULTY_HOD = "FACULTY_HOD"
    FACULTY_LIST = "FACULTY_LIST"
    FACULTY_SEARCH = "FACULTY_SEARCH"
    FACULTY_PROFILE = "FACULTY_PROFILE"
    FACULTY_SUBJECT = "FACULTY_SUBJECT"
    FACULTY_COUNT = "FACULTY_COUNT"
    FACULTY_RESEARCH = "FACULTY_RESEARCH"
    AMBIGUOUS_CLARIFICATION = "AMBIGUOUS_CLARIFICATION"

    # Conversational & Scope
    SMALL_TALK = "SMALL_TALK"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"  # Reserved for malicious / abuse queries
    ESCALATE = "ESCALATE"
    UNKNOWN = "UNKNOWN"


class IntentResult(BaseModel):
    intent: IntentEnum
    confidence: float = Field(ge=0.0, le=1.0)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    routed_by: str = "heuristic_fast_path"
    mode: RouterMode = RouterMode.COLLEGE_MODE


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

    def resolve_conversational_context(
        self,
        query: str,
        history: Optional[List[Dict[str, str]]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Analyzes conversation history to resolve contextual follow-ups like:
        - "Which one is related to AI?" after "What are the upcoming events?"
        - "When is it?" after referring to a specific event
        - "Show photos" after talking about an event
        """
        context_params: Dict[str, Any] = {}
        if not history:
            return query, context_params

        # Look at last assistant message and user message
        last_user = ""
        last_assistant = ""
        for turn in reversed(history):
            role = turn.get("role", "")
            content = turn.get("content", "")
            if role == "user" and not last_user:
                last_user = content.lower()
            elif role == "assistant" and not last_assistant:
                last_assistant = content.lower()
            if last_user and last_assistant:
                break

        q_lower = query.lower().strip()

        # Follow-up: Filtering recent events (e.g. "which one is related to AI?", "is there any AI event?")
        if re.search(r"\b(which (one|event)|any of them|related to|filter for)\b", q_lower):
            if any(k in last_user for k in ["event", "fest", "workshop", "hackathon"]) or "upcoming event" in last_assistant or "past event" in last_assistant:
                context_params["is_followup_event_filter"] = True
                match = re.search(r"\b(related to|about|on|for)\s+([a-zA-Z0-9\s]+)", q_lower)
                if match:
                    context_params["filter_keyword"] = match.group(2).strip()

        # Follow-up: "When is it?" / "What is the date?" referring to a previously mentioned event
        if re.search(r"\b(when is it|what (is the )?date|when does it start|timing of that|where is it)\b", q_lower):
            combined_hist = (last_user + " " + last_assistant).lower()
            if any(k in combined_hist for k in ["event", "workshop", "hackathon", "fest", "program", "cse-ai", "ai"]):
                context_params["is_followup_event_date"] = True

        # Follow-up: "Show photos" / "Show me photos of that"
        if re.search(r"\b(show (me )?photos?|show (me )?pictures?|photos? of (it|that))\b", q_lower):
            context_params["is_followup_photos"] = True

        # Extract faculty and department context from conversation history
        from backend.app.services.query_normalizer import query_normalizer
        hist_ctx = query_normalizer.extract_context_from_history(history)
        context_params.update(hist_ctx)

        return query, context_params

    def route_fast_path(self, query: str, context_params: Optional[Dict[str, Any]] = None) -> Optional[IntentResult]:
        """High-precision, deterministic regex patterns for zero-latency routing."""
        q = query.lower().strip()
        params = context_params or {}

        # 1. Escalation / Grievances / Distress / Anti-Ragging
        if re.search(r"\b(ragging|harassment|suicide|emergency|distress|complaint against|police|grievance|depression|counselor|squad)\b", q):
            return IntentResult(
                intent=IntentEnum.ESCALATE,
                confidence=1.0,
                parameters={"type": "grievance_or_emergency"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 2. Malicious Abuse / Hacking Protection (Scope Guard for security violations only)
        if re.search(r"\b(hack (the )?system|drop table|dump database|steal (password|token)|inject sql|bypass auth|create malware|exploit)\b", q):
            return IntentResult(
                intent=IntentEnum.OUT_OF_SCOPE,
                confidence=0.98,
                parameters={"type": "security_violation"},
                mode=RouterMode.GENERAL_AI_MODE
            )

        # 3. Conversational Follow-Up Fast Paths
        if params.get("is_followup_photos"):
            return IntentResult(
                intent=IntentEnum.EVENT_PHOTOS,
                confidence=0.95,
                parameters={"topic": "event_photos", "followup": True},
                mode=RouterMode.COLLEGE_MODE
            )

        if params.get("is_followup_event_filter") or params.get("is_followup_event_date"):
            return IntentResult(
                intent=IntentEnum.EVENT_DETAILS,
                confidence=0.95,
                parameters={"followup": True, **params},
                mode=RouterMode.COLLEGE_MODE
            )

        # 4. Hybrid Queries (Combining College & External / Multi-source)
        # E.g. "Tell me about the upcoming AI workshop and who I can contact"
        if (re.search(r"\b(event|workshop|hackathon|fest)\b", q) and
            re.search(r"\b(who (can i|i can|to|should i) contact|contact details|contact person|how to contact|how can i contact|faculty|coordinator|phone|email)\b", q)):
            return IntentResult(
                intent=IntentEnum.HYBRID_QUERY,
                confidence=0.96,
                parameters={"sub_intents": ["EVENTS_UPCOMING", "PHONE_DIRECTORY"]},
                mode=RouterMode.HYBRID_MODE
            )

        # E.g. "MLRITM capstone project ideas" or "Project ideas for college students"
        if re.search(r"\b(project idea|ideas for.*project|capstone project|final year project)\b", q):
            if "mlritm" in q or "college" in q:
                return IntentResult(
                    intent=IntentEnum.PROJECT_IDEAS,
                    confidence=0.95,
                    parameters={"domain": "project_ideas", "hybrid": True},
                    mode=RouterMode.HYBRID_MODE
                )
            return IntentResult(
                intent=IntentEnum.PROJECT_IDEAS,
                confidence=0.95,
                parameters={"domain": "project_ideas"},
                mode=RouterMode.GENERAL_AI_MODE
            )

        # 5. Small Talk / Greetings
        if re.search(r"\b(hi|hello|hey|good (morning|afternoon|evening)|how are you|who are you|thanks?|thank you)\b", q) and not re.search(r"\b(event|fee|exam|attendance|result|schedule|syllabus|hod|fest|faculty|course|calendar|python|code|docker|quantum)\b", q):
            return IntentResult(
                intent=IntentEnum.SMALL_TALK,
                confidence=0.99,
                parameters={},
                mode=RouterMode.COLLEGE_MODE
            )

        # 6. Official Event Photos & Galleries
        if re.search(r"\b(show (me )?photos?|photos? (from|of)|pictures? (from|of)|event photos?|fest photos?|gallery|show (me )?images?)\b", q):
            return IntentResult(
                intent=IntentEnum.EVENT_PHOTOS,
                confidence=0.98,
                parameters={"topic": "event_photos"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 7. Upcoming Events
        if re.search(r"\b(upcoming (college )?events?|events? (are )?(coming up|upcoming)|next (college )?event|date of the upcoming event|future events?|upcoming (college )?fests?|what (is|are) (the )?upcoming (college )?events?)\b", q):
            return IntentResult(
                intent=IntentEnum.EVENTS_UPCOMING,
                confidence=0.98,
                parameters={"metric": "upcoming_events"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 8. Past Events / Previous Fests
        if re.search(r"\b(past events?|previous (college )?events?|events? happened last month|last (college )?fest|when was the last fest|events? from 202[456]|what happened at the previous event|previous technical event|2024 (college )?fest|2025 (college )?fest)\b", q):
            return IntentResult(
                intent=IntentEnum.EVENTS_PAST,
                confidence=0.98,
                parameters={"metric": "past_events"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 9. Event Details
        if re.search(r"\b(information about the event|tell me about the event|event details|about (valarous|valorous|ranakrida|sangram|hackathon))\b", q):
            return IntentResult(
                intent=IntentEnum.EVENT_DETAILS,
                confidence=0.95,
                parameters={"topic": "event_details"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 10. Personal Faculty teaching a subject
        if re.search(r"\b(my faculty|who are my faculty|who is my .* faculty|who teaches|who is teaching|who handles|my teacher|my professor|class teacher|subject teacher)\b", q) and not re.search(r"\b(hod of|who is the hod|it hod|cse hod|ds hod|csm hod|ece hod|eee hod|civil hod|mech(anical)? hod|mba hod|faculty contact|contact number|faculty (phone|email)|give me the .* number|how can i contact .* hod|placement officer|who should i contact for scholarships?|dean student affairs|dean academics|phone directory|directory number|officer contact)\b", q):
            return IntentResult(
                intent=IntentEnum.FACULTY,
                confidence=0.98,
                parameters={"metric": "faculty"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 11. Authoritative MLRITM Faculty Intelligence & Short Query Understanding
        from backend.app.services.query_normalizer import query_normalizer
        norm_res = query_normalizer.understand_query(query, conversation_context=params)

        if norm_res.is_ambiguous:
            return IntentResult(
                intent=IntentEnum.AMBIGUOUS_CLARIFICATION,
                confidence=norm_res.confidence,
                parameters={
                    "understanding": norm_res.model_dump(),
                    "clarification_prompt": norm_res.clarification_prompt,
                    **norm_res.entities,
                },
                routed_by="query_normalizer",
                mode=RouterMode.COLLEGE_MODE,
            )

        faculty_intent_map = {
            "FACULTY_HOD": IntentEnum.FACULTY_HOD,
            "FACULTY_LIST": IntentEnum.FACULTY_LIST,
            "FACULTY_SEARCH": IntentEnum.FACULTY_SEARCH,
            "FACULTY_PROFILE": IntentEnum.FACULTY_PROFILE,
            "FACULTY_SUBJECT": IntentEnum.FACULTY_SUBJECT,
            "FACULTY_COUNT": IntentEnum.FACULTY_COUNT,
            "FACULTY_RESEARCH": IntentEnum.FACULTY_RESEARCH,
        }
        if norm_res.intent in faculty_intent_map and norm_res.confidence >= 0.85:
            return IntentResult(
                intent=faculty_intent_map[norm_res.intent],
                confidence=norm_res.confidence,
                parameters={
                    "understanding": norm_res.model_dump(),
                    "raw_query": norm_res.raw_query,
                    "normalized_query": norm_res.normalized_query,
                    **norm_res.entities,
                },
                routed_by="query_normalizer",
                mode=RouterMode.COLLEGE_MODE,
            )

        # 12. Administrative Officers / Phone Directory Contacts
        if re.search(r"\b(hod of|who is the hod|it hod|cse hod|ds hod|csm hod|ece hod|eee hod|civil hod|mech(anical)? hod|mba hod|faculty contact|contact number|faculty (phone|email)|give me the .* number|how can i contact .* hod|placement officer|who should i contact for scholarships?|dean student affairs|dean academics|phone directory|directory number|officer contact)\b", q):
            return IntentResult(
                intent=IntentEnum.PHONE_DIRECTORY,
                confidence=0.98,
                parameters={"topic": "phone_directory"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 12. Placements
        if re.search(r"\b(placements?|placement record|placement stats?|top recruiters?|highest package|average package|campus placements?)\b", q):
            return IntentResult(
                intent=IntentEnum.PLACEMENTS,
                confidence=0.95,
                parameters={"topic": "placements"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 13. Scholarships
        if re.search(r"\b(scholarships?|epass|fee reimbursement|merit scholarship)\b", q) and not re.search(r"\b(my scholarship|what scholarship (am i|do i))\b", q):
            return IntentResult(
                intent=IntentEnum.SCHOLARSHIP,
                confidence=0.95,
                parameters={"topic": "scholarships"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 14. Admissions
        if re.search(r"\b(admissions?|admission details|eamcet code|convener quota|management quota|how to get admission|how to join|intake capacity)\b", q) and not re.search(r"\b(my admission|what is my admission)\b", q):
            return IntentResult(
                intent=IntentEnum.ADMISSIONS,
                confidence=0.95,
                parameters={"topic": "admissions"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 15. Courses & Departments Available
        if re.search(r"\b(departments? (are )?available|courses? (are )?available|what courses|what branches|b\.?tech branches|programs? offered|intake)\b", q):
            return IntentResult(
                intent=IntentEnum.COURSES,
                confidence=0.95,
                parameters={"topic": "courses"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 16. Notices & Circulars
        if re.search(r"\b(notices? (were )?recently published|recent notices?|latest circulars?|announcements?|new circulars?)\b", q):
            return IntentResult(
                intent=IntentEnum.NOTICES,
                confidence=0.95,
                parameters={"topic": "notices"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 17. Academic Calendar & Holidays
        if re.search(r"\b(academic calendar|semester calendar|b\.?tech calendar)\b", q):
            return IntentResult(
                intent=IntentEnum.ACADEMIC_CALENDAR,
                confidence=0.95,
                parameters={"metric": "academic_calendar"},
                mode=RouterMode.COLLEGE_MODE
            )

        if re.search(r"\b(holidays?|is tomorrow a holiday|vacations?|working days? left|semester end exams? dates?|exam dates?|upcoming holidays|list of holidays)\b", q):
            return IntentResult(
                intent=IntentEnum.HOLIDAYS,
                confidence=0.95,
                parameters={"metric": "calendar"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 18. College General Info (Address, Location, Contact)
        if re.search(r"\b(college address|where is mlritm located|campus location|how to reach|contact email|college phone|about mlritm|autonomous)\b", q):
            return IntentResult(
                intent=IntentEnum.GENERAL_COLLEGE,
                confidence=0.95,
                parameters={"topic": "general_info"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 19. Personal Student Attendance
        if re.search(r"\b(my(\s+\w+)?\s+attendance|attendance\b.*(percentage|shortage|short)|am i detained|classes (do i need|needed)|reach 75%|which subjects? (have|has).*(low|lowest|poor|bad|short)|lowest attendance|attendance in\b|\battendance\b)\b", q) and not re.search(r"\b(policy|rules?|regulation|condonation)\b", q):
            return IntentResult(
                intent=IntentEnum.ATTENDANCE,
                confidence=0.98,
                parameters={"metric": "attendance"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 20. Personal Student Internal Marks
        if re.search(r"\b(internal marks?|internals|mid.?term marks?|mid.?[12]\s+marks?|marks? in mid|sessional marks?|assignment marks?|my marks?|cie marks?)\b", q):
            return IntentResult(
                intent=IntentEnum.MARKS,
                confidence=0.96,
                parameters={"metric": "internal_marks"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 21. Personal Semester Examination Results
        if re.search(r"\b(my(\s+\w+){0,2}\s+results?|results?\b.*\bthis semester|sgpa|cgpa|grades? in|my grades?|did i pass|semester marks?|backlogs?)\b", q):
            return IntentResult(
                intent=IntentEnum.RESULTS,
                confidence=0.98,
                parameters={"metric": "results"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 22. Personal Examination Schedule
        if re.search(r"\b(exam schedule|examination schedule|next exam|upcoming exams?|my exams?|hall ticket|exam time ?table|when (is|are) (my|the) exams?|mid.?[12]\b)\b", q):
            return IntentResult(
                intent=IntentEnum.EXAMS,
                confidence=0.95,
                parameters={"metric": "exams"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 23. Personal Class Timetable
        if re.search(r"\b(timetable|class timetable|my timetable|next class|which room|lab start|next lecture|period timing|class today|classes today|classes tomorrow)\b", q):
            return IntentResult(
                intent=IntentEnum.TIMETABLE,
                confidence=0.95,
                parameters={"metric": "timetable"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 24. Personal Enrolled Subjects
        if re.search(r"\b(my subjects?|which subjects? (am i|i am) (studying|taking|doing|learning)|my courses?|subjects? this semester|current semester subjects?|my curriculum|subject list)\b", q):
            return IntentResult(
                intent=IntentEnum.SUBJECTS,
                confidence=0.95,
                parameters={"metric": "subjects"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 25. Personal Assignments
        if re.search(r"\b(assignments?|submissions?|homework|due date|pending work)\b", q):
            return IntentResult(
                intent=IntentEnum.ASSIGNMENTS,
                confidence=0.94,
                parameters={"metric": "assignments"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 26. Personal Academic Profile & Specific Stored Attributes
        if re.search(r"\b(my fees?|fee status|fee due|fee payment|fee dues?|fee balance|paid fees?|pending fee)\b", q):
            return IntentResult(
                intent=IntentEnum.PROFILE,
                confidence=0.98,
                parameters={"metric": "fees"},
                mode=RouterMode.COLLEGE_MODE
            )

        if re.search(r"\b((my\s+)?profile(\s+data)?|academic profile|my details|student profile|profile details|my department|my branch|what is my branch|my section|my roll number|what is my roll number|which semester am i|what semester am i|my current semester|my academic (year|info|details)|who am i|my admission year|what is my admission year|my student email|what is my (student )?email|my student mobile|what is my (student )?mobile|my scholarship|what scholarship (am i|do i)|my father('s)? (profession|job|work|name|mobile|phone)|my father (profession|job|work|name|mobile|phone)|my mother('s)? (name|mobile|phone)|my mother (name|mobile|phone)|my parent('s)? (profession|income)|my admission category|my (dob|date of birth)|what is my caste)\b", q):
            return IntentResult(
                intent=IntentEnum.PROFILE,
                confidence=0.98,
                parameters={"metric": "profile"},
                mode=RouterMode.COLLEGE_MODE
            )

        # 27. Academic Regulations Policy (FAQ_RAG)
        if re.search(r"\b(can i get condonation|condonation if|condonation allowed|detention rules?|promotion criteria|credits? needed|eligib|tuition fee|fee for|fee structure|regulation|grading system)\b", q):
            return IntentResult(
                intent=IntentEnum.FAQ_RAG,
                confidence=0.92,
                parameters={"topic": "academic_regulations"},
                mode=RouterMode.COLLEGE_MODE
            )

        # ---------------------------------------------------------------------
        # 28. Programming & Technical Concepts (GENERAL_AI_MODE)
        # ---------------------------------------------------------------------
        if re.search(r"\b(explain recursion|recursion|python decorator|decorators? in python|docker|containerization|git commit|sql join|binary tree|algorithm|data structure|object oriented|polymorphism|pointers in c|how does .* work|code in python|write a function|debug this|syntax error|programming error)\b", q) and not ("mlritm" in q or "college" in q):
            return IntentResult(
                intent=IntentEnum.CODING_TECH,
                confidence=0.95,
                parameters={"topic": "programming"},
                mode=RouterMode.GENERAL_AI_MODE
            )

        # ---------------------------------------------------------------------
        # 29. Current Web Information (Temporal / Freshness) (GENERAL_AI_MODE)
        # ---------------------------------------------------------------------
        if (re.search(r"\b(latest version of|latest (openai|ai|python|technology|model)|what is the latest|latest news|today's news|current trend|trends in 202[56]|what happened today)\b", q)
            and not ("mlritm" in q or "college" in q)):
            return IntentResult(
                intent=IntentEnum.CURRENT_WEB_INFO,
                confidence=0.95,
                parameters={"temporal": True},
                mode=RouterMode.GENERAL_AI_MODE
            )

        # ---------------------------------------------------------------------
        # 30. General Knowledge & Academic Science (GENERAL_AI_MODE)
        # ---------------------------------------------------------------------
        if re.search(r"\b(quantum computing|what is quantum|machine learning|artificial intelligence|deep learning|neural network|photosynthesis|theory of relativity|how airplanes fly|blockchain|cryptography|compiler design|operating system|cloud computing)\b", q) and not ("mlritm" in q or "college" in q):
            return IntentResult(
                intent=IntentEnum.GENERAL_KNOWLEDGE,
                confidence=0.94,
                parameters={"domain": "general_knowledge"},
                mode=RouterMode.GENERAL_AI_MODE
            )

        return None

    def route(self, query: str, history: Optional[List[Dict[str, str]]] = None) -> IntentResult:
        """
        Routes student query using conversational context, fast-path, or intelligent mode selection.
        Guarantees that general/out-of-box questions are routed to GENERAL_AI_MODE instead of being rejected.
        """
        resolved_q, context_params = self.resolve_conversational_context(query, history)
        fast_result = self.route_fast_path(resolved_q, context_params)
        if fast_result and (fast_result.intent == IntentEnum.AMBIGUOUS_CLARIFICATION or fast_result.confidence >= 0.85):
            return fast_result

        q_lower = query.lower()

        # College-specific keyword indicators
        college_keywords = [
            "mlritm", "dundigal", "autonomous", "mlrs", "anvaya", "campus",
            "seminar", "maker space", "innovation", "facility", "facilities",
            "timing", "timings", "hostel", "canteen", "bus", "library", "club", "robotics"
        ]
        if any(k in q_lower for k in college_keywords):
            if any(k in q_lower for k in ["event", "fest", "workshop", "hackathon"]):
                return IntentResult(intent=IntentEnum.EVENTS_UPCOMING, confidence=0.85, parameters={"query": query}, mode=RouterMode.COLLEGE_MODE)
            if any(k in q_lower for k in ["hod", "faculty", "professor", "contact", "email", "phone", "director", "principal"]):
                return IntentResult(intent=IntentEnum.PHONE_DIRECTORY, confidence=0.85, parameters={"query": query}, mode=RouterMode.COLLEGE_MODE)
            if any(k in q_lower for k in ["fee", "regulation", "credit", "pass mark", "grading", "syllabus"]):
                return IntentResult(intent=IntentEnum.FAQ_RAG, confidence=0.85, parameters={"query": query}, mode=RouterMode.COLLEGE_MODE)
            return IntentResult(intent=IntentEnum.GENERAL_COLLEGE, confidence=0.85, parameters={"query": query}, mode=RouterMode.COLLEGE_MODE)

        # Technical, Programming, and AI questions
        if any(k in q_lower for k in ["python", "java", "c++", "javascript", "code", "function", "docker", "api", "database", "git", "linux", "compiler", "recursion", "decorator"]):
            return IntentResult(intent=IntentEnum.CODING_TECH, confidence=0.85, parameters={"query": query}, mode=RouterMode.GENERAL_AI_MODE)

        # Current Information queries
        if any(k in q_lower for k in ["latest", "current", "today", "recent", "newest", "2026", "news", "trend"]):
            return IntentResult(intent=IntentEnum.CURRENT_WEB_INFO, confidence=0.85, parameters={"query": query}, mode=RouterMode.GENERAL_AI_MODE)

        # General Knowledge fallback (Open-ended query resolution)
        return IntentResult(intent=IntentEnum.GENERAL_KNOWLEDGE, confidence=0.80, parameters={"query": query}, mode=RouterMode.GENERAL_AI_MODE)
