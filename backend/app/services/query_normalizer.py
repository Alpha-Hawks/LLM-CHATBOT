"""
Query Understanding & Normalization Platform.

Implements:
1. Department Canonical Alias Engine (CSE, IT, DS, AIML, Cyber, ECE, EEE, Mech, Civil, FE, MBA)
2. Typo and Variation Normalizer (faclty, csee, engish)
3. Telugu-English and Student Slang Parser (cse lo hod evaru, bro cse hod, evaru)
4. Query Understanding Object (raw_query, normalized_query, intent, entities, confidence)
5. Fuzzy matching with configurable confidence thresholds (>=0.85 auto, 0.60-0.84 candidates, <0.60 clarification)
6. Ambiguous short query detection (e.g. "english" -> clarification)
"""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class QueryUnderstandingResult(BaseModel):
    raw_query: str
    normalized_query: str
    intent: str
    entities: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    is_ambiguous: bool = False
    clarification_prompt: Optional[str] = None
    expanded_search_terms: List[str] = Field(default_factory=list)


# Canonical Department Dictionary
DEPARTMENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "CSE": {
        "code": "CSE",
        "name": "Computer Science and Engineering",
        "hod_name": "Dr. K Abdul Basith",
        "hod_designation": "Associate Professor & Head",
        "hod_email": "hodcse@mlritm.ac.in",
        "aliases": [
            "cse", "cs", "computer science", "computer science engineering",
            "computer science and engineering", "computer science & engineering",
            "cse core", "cse department", "dept of cse", "btech cse", "csee", "csse"
        ],
    },
    "IT": {
        "code": "IT",
        "name": "Information Technology",
        "hod_name": "Dr. M Nagalakshmi",
        "hod_designation": "Professor & Head",
        "hod_email": "hodit@mlritm.ac.in",
        "aliases": [
            "it", "information technology", "information tech", "infotech",
            "it dept", "it department", "dept of it", "btech it"
        ],
    },
    "CSE-AI-ML": {
        "code": "CSE-AI-ML",
        "name": "Computer Science and Engineering (AI & ML)",
        "hod_name": "Dr. B Ravi Prasad",
        "hod_designation": "Dean Academics & HOD-CSE (AI & ML)",
        "hod_email": "hodcsm@mlritm.ac.in",
        "aliases": [
            "aiml", "ai ml", "ai&ml", "ai/ml", "cse aiml", "cse ai ml",
            "cse-aiml", "artificial intelligence", "machine learning",
            "ai and ml", "ai & ml", "csm", "cse(ai&ml)", "cse (ai & ml)"
        ],
    },
    "CSE-DATA-SCIENCE": {
        "code": "CSE-DATA-SCIENCE",
        "name": "Computer Science and Engineering (Data Science)",
        "hod_name": "Dr. B Srikantha Setty",
        "hod_designation": "Associate Professor & HOD",
        "hod_email": "hodcsd@mlritm.ac.in",
        "aliases": [
            "ds", "data science", "datascience", "cse ds", "cse data science",
            "cse-ds", "csd", "cse(ds)", "cse (data science)", "data science department"
        ],
    },
    "CSE-CYBER-SECURITY": {
        "code": "CSE-CYBER-SECURITY",
        "name": "Computer Science and Engineering (Cyber Security)",
        "hod_name": "Dr. M Venkat Reddy",
        "hod_designation": "Professor & Head",
        "hod_email": "hodcyber@mlritm.ac.in",
        "aliases": [
            "cyber", "cyber security", "cybersecurity", "cse cyber", "cse cybersecurity",
            "csc", "cyber security dept", "cyber dept", "cse-cyber"
        ],
    },
    "ECE": {
        "code": "ECE",
        "name": "Electronics and Communication Engineering",
        "hod_name": "Dr. P Venkata Ramana",
        "hod_designation": "Professor & Head",
        "hod_email": "hodece@mlritm.ac.in",
        "aliases": [
            "ece", "electronics", "electronics communication",
            "electronics and communication", "electronics & communication",
            "ece dept", "dept of ece", "btech ece"
        ],
    },
    "EEE": {
        "code": "EEE",
        "name": "Electrical and Electronics Engineering",
        "hod_name": "Dr. K Niranjan",
        "hod_designation": "Professor & Head",
        "hod_email": "hodeee@mlritm.ac.in",
        "aliases": [
            "eee", "electrical", "electrical electronics",
            "electrical and electronics", "electrical & electronics",
            "eee dept", "dept of eee", "btech eee"
        ],
    },
    "MECHANICAL": {
        "code": "MECHANICAL",
        "name": "Mechanical Engineering",
        "hod_name": "Dr. G Surya Prakash Rao",
        "hod_designation": "Professor & Head",
        "hod_email": "hodmech@mlritm.ac.in",
        "aliases": [
            "mech", "mechanical", "mechanical engineering", "mech eng",
            "mech dept", "dept of mech", "btech mech", "me branch", "me dept"
        ],
    },
    "CIVIL": {
        "code": "CIVIL",
        "name": "Civil Engineering",
        "hod_name": "Dr. S P Jani",
        "hod_designation": "Professor & Head",
        "hod_email": "hodcivil@mlritm.ac.in",
        "aliases": [
            "civil", "civil engineering", "civil eng", "ce dept", "dept of civil", "btech civil", "ce branch"
        ],
    },
    "FRESHMAN-ENGINEERING": {
        "code": "FRESHMAN-ENGINEERING",
        "name": "Freshman Engineering",
        "hod_name": "Dr. Lakshmi Sowjanya",
        "hod_designation": "Professor & Head",
        "hod_email": "hodfe@mlritm.ac.in",
        "aliases": [
            "fe", "freshman", "freshman engineering", "humanities and sciences",
            "h&s", "hns", "first year", "1st year", "freshers", "fed"
        ],
    },
    "MBA": {
        "code": "MBA",
        "name": "Masters in Business Administration",
        "hod_name": "Dr. N Revathi",
        "hod_designation": "Professor & Head",
        "hod_email": "hodmba@mlritm.ac.in",
        "aliases": [
            "mba", "management", "business administration", "masters in business administration"
        ],
    },
}

# Common Telugu / Informal Student Vocabulary
TELUGU_SLANG_TERMS = {
    "evaru": "who",
    "evvaru": "who",
    "evaru?": "who",
    "lo": "in",
    "cheppandi": "tell me",
    "cheppu": "tell",
    "unaru": "are there",
    "unnaru": "are there",
    "undhi": "is there",
    "undi": "is there",
    "bro": "",
    "bhayya": "",
    "bhai": "",
    "sir": "",
    "madam": "",
    "plz": "please",
    "pls": "please",
}

TYPO_CORRECTIONS = {
    "faclty": "faculty",
    "facult": "faculty",
    "facultys": "faculty",
    "faculties": "faculty",
    "facuty": "faculty",
    "faculity": "faculty",
    "techer": "teacher",
    "techers": "teachers",
    "profesor": "professor",
    "professers": "professors",
    "profs": "professors",
    "proff": "professor",
    "csee": "cse",
    "csse": "cse",
    "cseee": "cse",
    "engish": "english",
    "phy": "physics",
    "math": "mathematics",
    "maths": "mathematics",
    "itdept": "it",
    "informaton": "information",
    "h.o.d": "hod",
    "h.o.d.": "hod",
    "headd": "head",
    "basit": "basith",
    "abdulbasith": "abdul basith",
}

# Subjects that might be ambiguous as a single word
AMBIGUOUS_SINGLE_TERMS = {
    "english": ("English faculty", "information about the English subject"),
    "physics": ("Physics faculty", "information about the Physics course"),
    "chemistry": ("Chemistry faculty", "information about the Chemistry course"),
    "maths": ("Mathematics faculty", "information about the Mathematics subject"),
    "mathematics": ("Mathematics faculty", "information about the Mathematics subject"),
}


class QueryNormalizer:
    def __init__(self):
        # Build inverted alias lookup table
        self.alias_map: Dict[str, Tuple[str, str]] = {}
        for code, info in DEPARTMENT_REGISTRY.items():
            for alias in info["aliases"]:
                self.alias_map[alias.lower().strip()] = (code, info["name"])

    def normalize_text(self, text: str) -> str:
        """Lowercases, normalizes unicode, handles typos, dotted acronyms, and student slang."""
        if not text:
            return ""
        # Unicode normalization
        clean = unicodedata.normalize("NFKD", text).strip().lower()

        # Dotted acronym normalization before stripping punctuation
        clean = re.sub(r"\bh\s*\.?\s*o\s*\.?\s*d\b\.?", "hod", clean)
        clean = re.sub(r"\bc\s*\.?\s*s\s*\.?\s*e\b\.?", "cse", clean)
        clean = re.sub(r"\bi\s*\.?\s*t\b\.?", "it", clean)
        clean = re.sub(r"\be\s*\.?\s*c\s*\.?\s*e\b\.?", "ece", clean)
        clean = re.sub(r"\be\s*\.?\s*e\s*\.?\s*e\b\.?", "eee", clean)
        clean = re.sub(r"\bb\s*\.?\s*tech\b", "btech", clean)

        # Remove punctuation except hyphen and slash
        clean = re.sub(r"[^\w\s\-\/\&]", " ", clean)

        # Word-by-word typo and slang replacement
        words = clean.split()
        normalized_words = []
        for w in words:
            if w in TYPO_CORRECTIONS:
                normalized_words.append(TYPO_CORRECTIONS[w])
            elif w in TELUGU_SLANG_TERMS:
                val = TELUGU_SLANG_TERMS[w]
                if val:
                    normalized_words.append(val)
            else:
                normalized_words.append(w)

        res = " ".join(normalized_words)
        # Collapse multiple spaces
        return re.sub(r"\s+", " ", res).strip()

    def resolve_department(self, text: str) -> Optional[Tuple[str, str, float]]:
        """
        Resolves canonical department code, name, and match confidence from text.
        Returns: (code, name, confidence) or None.
        """
        clean = self.normalize_text(text)
        words = clean.split()

        # 1. Exact alias match in dictionary
        if clean in self.alias_map:
            code, name = self.alias_map[clean]
            return code, name, 1.0

        # 2. Check for multi-word aliases inside text (ordered by longest alias first)
        sorted_aliases = sorted(self.alias_map.keys(), key=lambda x: len(x), reverse=True)
        for alias in sorted_aliases:
            pattern = r"\b" + re.escape(alias) + r"\b"
            if re.search(pattern, clean):
                code, name = self.alias_map[alias]
                return code, name, 0.98

        # 3. Fuzzy matching for department typos (e.g. "csee", "mechaniacl")
        best_match = None
        best_ratio = 0.0
        for w in words:
            if len(w) >= 3:
                for alias, (code, name) in self.alias_map.items():
                    ratio = SequenceMatcher(None, w, alias).ratio()
                    if ratio > best_ratio and ratio >= 0.85:
                        best_ratio = ratio
                        best_match = (code, name, ratio)

        return best_match

    def understand_query(
        self,
        raw_query: str,
        student_department: Optional[str] = None,
        conversation_context: Optional[Dict[str, Any]] = None,
    ) -> QueryUnderstandingResult:
        """
        Analyzes student query to extract intent, entities, and confidence score.
        """
        norm_q = self.normalize_text(raw_query)
        words = norm_q.split()

        entities: Dict[str, Any] = {}
        confidence = 0.95
        intent = "UNKNOWN"
        is_ambiguous = False
        clarification_prompt = None
        expanded_terms = []

        # ---------------------------------------------------------------------
        # 1. Ambiguous Single Term Detection (e.g. "english", "physics")
        # ---------------------------------------------------------------------
        if norm_q in AMBIGUOUS_SINGLE_TERMS and not conversation_context:
            opt1, opt2 = AMBIGUOUS_SINGLE_TERMS[norm_q]
            return QueryUnderstandingResult(
                raw_query=raw_query,
                normalized_query=norm_q,
                intent="AMBIGUOUS_QUERY",
                entities={"term": norm_q},
                confidence=0.50,
                is_ambiguous=True,
                clarification_prompt=f"Do you want the {opt1} or {opt2}?",
                expanded_search_terms=[norm_q, opt1, opt2],
            )

        # ---------------------------------------------------------------------
        # 2. "MY" Context Resolution (e.g. "my hod", "my faculty", "who is my hod")
        # ---------------------------------------------------------------------
        is_my_query = bool(re.search(r"\b(my|our)\b", norm_q))
        dept_match = self.resolve_department(norm_q)

        if is_my_query and not dept_match and student_department:
            dept_match = self.resolve_department(student_department)
            entities["resolved_from_student_session"] = True

        if dept_match:
            dept_code, dept_name, dept_conf = dept_match
            entities["department_code"] = dept_code
            entities["department"] = dept_name
            entities["department_confidence"] = dept_conf
            expanded_terms.extend([dept_code, dept_name])

        # ---------------------------------------------------------------------
        # 3. Dedicated HOD Intent Detection
        # ---------------------------------------------------------------------
        is_hod_query = bool(
            re.search(
                r"\b(hod|head|department head|dept head|heads|who heads|head of department|head of the department)\b",
                norm_q,
            )
        )
        if is_hod_query:
            intent = "FACULTY_HOD"
            if not dept_match and conversation_context and conversation_context.get("last_department_code"):
                last_code = conversation_context["last_department_code"]
                dept_match = self.resolve_department(last_code)
                if dept_match:
                    entities["department_code"] = dept_match[0]
                    entities["department"] = dept_match[1]
                    entities["department_confidence"] = dept_match[2]
                    entities["resolved_from_context"] = True

            confidence = 0.98 if dept_match else 0.88
            if dept_match:
                dept_code = dept_match[0]
                if dept_code in DEPARTMENT_REGISTRY:
                    entities["official_hod"] = DEPARTMENT_REGISTRY[dept_code]["hod_name"]
                    entities["official_designation"] = DEPARTMENT_REGISTRY[dept_code]["hod_designation"]
                    entities["official_email"] = DEPARTMENT_REGISTRY[dept_code]["hod_email"]
            expanded_terms.extend(["Head of Department", "HOD", "Associate Professor & Head", "Professor & Head"])
            return QueryUnderstandingResult(
                raw_query=raw_query,
                normalized_query=norm_q,
                intent=intent,
                entities=entities,
                confidence=confidence,
                expanded_search_terms=expanded_terms,
            )

        # ---------------------------------------------------------------------
        # 4. Faculty Count / Quantitative Inquiries
        # ---------------------------------------------------------------------
        if re.search(r"\b(how many|count of|number of|total)\b.*\b(faculty|professors?|teachers?|staff)\b", norm_q) or re.search(r"\b(how many faculty|how many professors)\b", norm_q):
            intent = "FACULTY_COUNT"
            confidence = 0.96
            return QueryUnderstandingResult(
                raw_query=raw_query,
                normalized_query=norm_q,
                intent=intent,
                entities=entities,
                confidence=confidence,
                expanded_search_terms=expanded_terms,
            )

        # ---------------------------------------------------------------------
        # 5. Faculty Directory / List by Department or Designation
        # ---------------------------------------------------------------------
        is_faculty_list = bool(
            re.search(r"\b(faculty|professors?|assistant professors?|associate professors?|teachers?|staff|members)\b", norm_q)
            or re.search(r"\b(list|show|all|names? of)\b.*\b(faculty|professors?)\b", norm_q)
        )
        if dept_match and is_faculty_list:
            intent = "FACULTY_LIST"
            confidence = 0.98
            expanded_terms.extend(["Faculty Directory", "Professors", "Assistant Professors", "Associate Professors"])
            return QueryUnderstandingResult(
                raw_query=raw_query,
                normalized_query=norm_q,
                intent=intent,
                entities=entities,
                confidence=confidence,
                expanded_search_terms=expanded_terms,
            )

        # ---------------------------------------------------------------------
        # 6. Faculty Subject / Course Inquiries (e.g. "who teaches dbms", "which faculty teaches...", "os faculty")
        # ---------------------------------------------------------------------
        subject_match = re.search(
            r"\b(which faculty teaches|who teaches|who is teaching|who handles|which teacher handles|faculty for|teacher for)\s+([a-zA-Z\s0-9]+)",
            norm_q,
        )
        if subject_match or re.search(r"\b(dbms|os|operating systems?|data structures?|dsa|ai|ml|machine learning|python|java|c programming|cloud computing|network security|cyber security|physics|chemistry|english|mathematics|maths)\s+(faculty|teacher|professor)\b", norm_q):
            intent = "FACULTY_SUBJECT"
            confidence = 0.95
            subj = None
            if subject_match:
                subj = subject_match.group(2).strip()
            else:
                for s in ["dbms", "os", "operating systems", "data structures", "dsa", "ai", "ml", "machine learning", "python", "java", "network security", "physics", "chemistry", "english", "mathematics", "maths"]:
                    if s in norm_q:
                        subj = s.upper()
                        break
            entities["subject"] = subj or norm_q
            expanded_terms.extend([entities["subject"], "Courses Taught", "Faculty Assignment"])
            return QueryUnderstandingResult(
                raw_query=raw_query,
                normalized_query=norm_q,
                intent=intent,
                entities=entities,
                confidence=confidence,
                expanded_search_terms=expanded_terms,
            )

        # General faculty list when no department matched yet
        if is_faculty_list:
            intent = "FACULTY_LIST"
            confidence = 0.90
            expanded_terms.extend(["Faculty Directory", "Professors", "Assistant Professors", "Associate Professors"])
            return QueryUnderstandingResult(
                raw_query=raw_query,
                normalized_query=norm_q,
                intent=intent,
                entities=entities,
                confidence=confidence,
                expanded_search_terms=expanded_terms,
            )

        # ---------------------------------------------------------------------
        # 7. Faculty Research, Publications, Patents
        # ---------------------------------------------------------------------
        if re.search(r"\b(research|publications?|papers?|patents?|projects?|specialization|works in|working in)\b", norm_q):
            intent = "FACULTY_RESEARCH"
            confidence = 0.94
            if conversation_context and conversation_context.get("last_faculty_name"):
                entities["target_faculty"] = conversation_context["last_faculty_name"]
                entities["faculty_query"] = conversation_context["last_faculty_name"]
                entities["focus"] = "research"

            for area in ["ai", "machine learning", "cybersecurity", "cyber security", "data science", "iot", "robotics", "vlsi", "cloud", "network security"]:
                if area in norm_q:
                    entities["research_area"] = area.upper()
                    break
            expanded_terms.extend(["Research Publications", "Patents", "Area of Specialization"])
            return QueryUnderstandingResult(
                raw_query=raw_query,
                normalized_query=norm_q,
                intent=intent,
                entities=entities,
                confidence=confidence,
                expanded_search_terms=expanded_terms,
            )

        # ---------------------------------------------------------------------
        # 8. Specific Faculty Name or Profile Search (e.g. "basith", "abdul basith", "dr basith")
        # ---------------------------------------------------------------------
        known_faculty_tokens = [
            "basith", "basit", "abdul", "nagalakshmi", "sridhar", "murali",
            "prasad", "revathi", "jani", "niranjan", "ramana", "sowjanya",
            "venkat", "ashok", "kavitha", "suresh"
        ]
        found_faculty = any(t in norm_q for t in known_faculty_tokens)
        if found_faculty or re.search(r"\b(dr\b|prof\b|professor\b|mr\b|mrs\b|ms\b)", norm_q):
            intent = "FACULTY_SEARCH"
            confidence = 0.95
            entities["faculty_query"] = raw_query
            expanded_terms.extend(["Faculty Profile", "Qualifications", "Official Profile"])
            return QueryUnderstandingResult(
                raw_query=raw_query,
                normalized_query=norm_q,
                intent=intent,
                entities=entities,
                confidence=confidence,
                expanded_search_terms=expanded_terms,
            )

        # ---------------------------------------------------------------------
        # 9. Follow-up Inquiries in Conversation (e.g. "qualifications?", "profile", "what is his specialization?", "what about his research?")
        # ---------------------------------------------------------------------
        if conversation_context and conversation_context.get("last_faculty_name"):
            last_faculty = conversation_context["last_faculty_name"]
            entities["target_faculty"] = last_faculty
            entities["faculty_query"] = last_faculty

            if re.search(r"\b(qualification|qualifications|degree|degrees|phd|education)\b", norm_q):
                intent = "FACULTY_PROFILE"
                entities["focus"] = "qualifications"
                return QueryUnderstandingResult(
                    raw_query=raw_query,
                    normalized_query=norm_q,
                    intent=intent,
                    entities=entities,
                    confidence=0.98,
                )
            if re.search(r"\b(specialization|speciality|field|domain|area of specialization)\b", norm_q):
                intent = "FACULTY_PROFILE"
                entities["focus"] = "specialization"
                return QueryUnderstandingResult(
                    raw_query=raw_query,
                    normalized_query=norm_q,
                    intent=intent,
                    entities=entities,
                    confidence=0.98,
                )
            if re.search(r"\b(experience|how many years|teaching experience)\b", norm_q):
                intent = "FACULTY_PROFILE"
                entities["focus"] = "experience"
                return QueryUnderstandingResult(
                    raw_query=raw_query,
                    normalized_query=norm_q,
                    intent=intent,
                    entities=entities,
                    confidence=0.98,
                )
            if re.search(r"\b(profile|link|url|website|page|view profile|show me the profile|show profile)\b", norm_q):
                intent = "FACULTY_PROFILE"
                entities["focus"] = "profile_link"
                return QueryUnderstandingResult(
                    raw_query=raw_query,
                    normalized_query=norm_q,
                    intent=intent,
                    entities=entities,
                    confidence=0.98,
                )
            if re.search(r"\b(research|publication|publications|papers|patents)\b", norm_q):
                intent = "FACULTY_RESEARCH"
                entities["focus"] = "research"
                return QueryUnderstandingResult(
                    raw_query=raw_query,
                    normalized_query=norm_q,
                    intent=intent,
                    entities=entities,
                    confidence=0.98,
                )
            if re.search(r"\b(tell me about (him|her|them)|about (him|her|them)|details|more info)\b", norm_q):
                intent = "FACULTY_SEARCH"
                return QueryUnderstandingResult(
                    raw_query=raw_query,
                    normalized_query=norm_q,
                    intent=intent,
                    entities=entities,
                    confidence=0.98,
                )

        # ---------------------------------------------------------------------
        # 10. Fallback / General
        # ---------------------------------------------------------------------
        return QueryUnderstandingResult(
            raw_query=raw_query,
            normalized_query=norm_q,
            intent="UNKNOWN",
            entities=entities,
            confidence=0.60,
            expanded_search_terms=[norm_q],
        )

    def extract_context_from_history(self, history: Optional[List[Dict[str, str]]]) -> Dict[str, Any]:
        """
        Scans recent chat history to identify:
        - The most recently discussed faculty member (e.g. Dr. K Abdul Basith)
        - The most recently discussed department (e.g. CSE)
        """
        context: Dict[str, Any] = {}
        if not history:
            return context

        for turn in reversed(history):
            content = turn.get("content", "")
            # Check for faculty names
            if "last_faculty_name" not in context:
                # 1. Match known registry HOD names
                for info in DEPARTMENT_REGISTRY.values():
                    if info["hod_name"].lower() in content.lower():
                        context["last_faculty_name"] = info["hod_name"]
                        break
                if "last_faculty_name" not in context:
                    # 2. Match pattern like Dr. First Last
                    match = re.search(r"\b((?:Dr|Prof)\.?\s+[A-Z][a-zA-Z\.]+(?:\s+[A-Z][a-zA-Z\.]+){1,3})\b", content)
                    if match:
                        context["last_faculty_name"] = match.group(1).strip()

            # Check for department mentions
            if "last_department_code" not in context:
                dept_res = self.resolve_department(content)
                if dept_res:
                    context["last_department_code"] = dept_res[0]

            if "last_faculty_name" in context and "last_department_code" in context:
                break

        return context


query_normalizer = QueryNormalizer()
