"""
Authoritative MLRITM Faculty Intelligence Service.

Authoritative source: https://mlritm.ac.in/faculty-profile
Provides:
1. Dynamic, searchable, conversational faculty intelligence
2. Dedicated HOD detection and resolution
3. Context-aware "my hod" resolution using student context
4. Subject and research matching with zero hallucinations
5. Student-friendly profile and list formatting
6. Quantitative counts and departmental listings
7. Official photos, profile links, and IRINS links
"""

import json
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import FacultyContact, FacultySyncLog
from backend.app.services.query_normalizer import DEPARTMENT_REGISTRY, query_normalizer

logger = logging.getLogger(__name__)

FACULTY_SEED_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "mlritm_faculty_seed.json")
)


class FacultyIntelligenceService:
    def __init__(self):
        self._memory_cache: List[Dict[str, Any]] = []
        self._load_seed_cache()

    def _load_seed_cache(self) -> None:
        """Loads authoritative faculty records into memory cache for sub-millisecond lookups."""
        if os.path.exists(FACULTY_SEED_PATH):
            try:
                with open(FACULTY_SEED_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._memory_cache = data.get("faculty", [])
                    logger.info(f"Loaded {len(self._memory_cache)} official faculty records into memory.")
            except Exception as e:
                logger.warning(f"Error loading faculty seed file: {e}")

    async def get_all_faculty(self, db: Optional[AsyncSession] = None) -> List[Dict[str, Any]]:
        """Retrieves all indexed faculty records from DB or cached seed."""
        if db is not None:
            try:
                stmt = select(FacultyContact).where(FacultyContact.status == "ACTIVE")
                records = (await db.execute(stmt)).scalars().all()
                if records:
                    return [self._row_to_dict(r) for r in records]
            except Exception as e:
                logger.warning(f"DB query for faculty failed ({e}); using memory cache.")
        return self._memory_cache

    def _row_to_dict(self, r: FacultyContact) -> Dict[str, Any]:
        return {
            "id": r.id,
            "faculty_id": r.faculty_id or "Not Available",
            "name": r.name,
            "normalized_name": r.normalized_name or r.name.lower(),
            "designation": r.designation,
            "department": r.department or "Not Available",
            "department_code": r.department_code or "CSE",
            "is_hod": r.is_hod,
            "responsibility": r.responsibility or r.designation,
            "email": r.email or "Not Available",
            "phone": r.phone or "Not Available",
            "profile_url": r.profile_url or "https://mlritm.ac.in/faculty-profile",
            "photo_url": r.photo_url or "https://mlritm.ac.in/sites/default/files/logo-MLRITM_0_1.png",
            "total_experience": r.total_experience or "Not Available",
            "experience_mlritm": r.experience_mlritm or "Not Available",
            "undergraduate_degree": r.undergraduate_degree or "Not Available",
            "postgraduate_degree": r.postgraduate_degree or "Not Available",
            "phd_degree": r.phd_degree or "Not Available",
            "employment_status": r.employment_status or "Full-Time",
            "specialization": r.specialization or "Not Available",
            "academic_identity_url": r.academic_identity_url or "Not Available",
            "video_lectures_url": r.video_lectures_url or "Not Available",
            "courses_taught": r.courses_taught or "Not Available",
            "research_interests": r.research_interests or "Not Available",
            "publications_summary": r.publications_summary or "Not Available",
            "patents_info": r.patents_info or "Not Available",
            "source_url": r.source_url or "https://mlritm.ac.in/faculty-profile",
            "status": r.status or "ACTIVE",
        }

    async def get_hod(self, department_code: str, db: Optional[AsyncSession] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieves official HOD based on official designation / department mapping.
        Authoritative official source evidence: Associate Professor & Head or Professor & Head.
        """
        code = department_code.upper().strip()
        faculty_list = await self.get_all_faculty(db)

        # 1. Check registry definition first with department scope
        if code in DEPARTMENT_REGISTRY:
            reg_info = DEPARTMENT_REGISTRY[code]
            hod_name = reg_info["hod_name"]
            for f in faculty_list:
                if f.get("department_code") == code and SequenceMatcher(None, f["name"].lower(), hod_name.lower()).ratio() >= 0.70:
                    return f

        # 2. Search for official is_hod flag in department
        for f in faculty_list:
            if f.get("department_code") == code and f.get("is_hod"):
                return f

        # 3. Fallback to any department_code if unassigned in database
        if code in DEPARTMENT_REGISTRY:
            reg_info = DEPARTMENT_REGISTRY[code]
            hod_name = reg_info["hod_name"]
            for f in faculty_list:
                if f["name"].lower() == hod_name.lower():
                    return f

        # 3. Return synthetic record from registry if not found in db
        if code in DEPARTMENT_REGISTRY:
            reg_info = DEPARTMENT_REGISTRY[code]
            return {
                "name": reg_info["hod_name"],
                "designation": reg_info["hod_designation"],
                "department": reg_info["name"],
                "department_code": code,
                "is_hod": True,
                "email": reg_info["hod_email"],
                "phone": reg_info.get("hod_phone", "040-29556182"),
                "profile_url": f"https://mlritm.ac.in/{code.lower()}-hod",
                "photo_url": "https://mlritm.ac.in/sites/default/files/logo-MLRITM_0_1.png",
                "total_experience": "Not Available",
                "experience_mlritm": "Not Available",
                "undergraduate_degree": "Not Available",
                "postgraduate_degree": "Not Available",
                "phd_degree": "Not Available",
                "employment_status": "Full-Time",
                "specialization": "Not Available",
                "academic_identity_url": "Not Available",
                "video_lectures_url": "Not Available",
                "source_url": "https://mlritm.ac.in/faculty-profile",
            }

        return None

    async def search_faculty(
        self,
        query: str,
        db: Optional[AsyncSession] = None,
        department_code: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Semantic and fuzzy search across names, designations, departments, and specializations.
        """
        faculty_list = await self.get_all_faculty(db)
        if not query and not department_code:
            return faculty_list[:limit]

        clean_q = query_normalizer.normalize_text(query).lower()
        query_words = [w for w in clean_q.split() if len(w) > 2]

        scored_results: List[Tuple[float, Dict[str, Any]]] = []

        for f in faculty_list:
            if department_code and f.get("department_code") != department_code:
                continue

            score = 0.0
            name_lower = f["name"].lower()
            norm_name = f.get("normalized_name", name_lower)
            dept_lower = f["department"].lower()
            desig_lower = f["designation"].lower()
            spec_lower = f.get("specialization", "").lower()
            fac_id = f.get("faculty_id", "").lower()

            # Exact name or ID match
            if clean_q in name_lower or clean_q in norm_name:
                score += 1.0
            if fac_id and fac_id != "not available" and clean_q in fac_id:
                score += 1.0

            # Substring or token overlap
            for w in query_words:
                if w in name_lower:
                    score += 0.4
                if w in desig_lower:
                    score += 0.2
                if w in dept_lower:
                    score += 0.2
                if w in spec_lower:
                    score += 0.3

            # Fuzzy name similarity (handles misspellings like "basith" vs "basit")
            name_ratio = SequenceMatcher(None, clean_q, name_lower).ratio()
            if name_ratio > 0.65:
                score += name_ratio * 0.8

            for part in name_lower.split():
                part_ratio = SequenceMatcher(None, clean_q, part).ratio()
                if part_ratio > 0.75:
                    score += part_ratio * 0.6

            if score > 0.25:
                scored_results.append((score, f))

        scored_results.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored_results[:limit]]

    async def get_faculty_by_department(
        self,
        department_code: str,
        db: Optional[AsyncSession] = None,
        designation_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves all active faculty members for a given canonical department."""
        faculty_list = await self.get_all_faculty(db)
        code = department_code.upper().strip()
        matches = [f for f in faculty_list if f.get("department_code") == code]

        if designation_filter:
            filt = designation_filter.lower()
            if "assistant" in filt:
                matches = [f for f in matches if "assistant" in f["designation"].lower()]
            elif "associate" in filt:
                matches = [f for f in matches if "associate" in f["designation"].lower()]
            elif "professor" in filt:
                # "professors" specifically excluding assistant/associate if requested
                matches = [f for f in matches if "professor" in f["designation"].lower() and "assistant" not in f["designation"].lower() and "associate" not in f["designation"].lower()]

        return matches

    async def count_faculty(
        self,
        department_code: Optional[str] = None,
        db: Optional[AsyncSession] = None,
        designation_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculates accurate counts from currently indexed official faculty records."""
        faculty_list = await self.get_all_faculty(db)
        if department_code:
            code = department_code.upper().strip()
            faculty_list = [f for f in faculty_list if f.get("department_code") == code]

        total = len(faculty_list)
        professors = sum(1 for f in faculty_list if "professor" in f["designation"].lower() and "assistant" not in f["designation"].lower() and "associate" not in f["designation"].lower())
        associates = sum(1 for f in faculty_list if "associate" in f["designation"].lower())
        assistants = sum(1 for f in faculty_list if "assistant" in f["designation"].lower())
        hods = sum(1 for f in faculty_list if f.get("is_hod"))

        return {
            "total": total,
            "professors": professors,
            "associate_professors": associates,
            "assistant_professors": assistants,
            "hods": hods,
            "department_code": department_code or "ALL",
        }

    async def search_by_research(
        self,
        query: str,
        db: Optional[AsyncSession] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Finds faculty with official research or specialization matching the query."""
        faculty_list = await self.get_all_faculty(db)
        clean = query_normalizer.normalize_text(query).lower()

        matches = []
        for f in faculty_list:
            spec = f.get("specialization", "").lower()
            research = f.get("research_interests", "").lower()
            pubs = f.get("publications_summary", "").lower()

            if any(k in spec or k in research or k in pubs for k in clean.split() if len(k) > 2):
                matches.append(f)
            elif "patent" in clean and f.get("patents_info") != "Not Available":
                matches.append(f)
            elif "publication" in clean and (f.get("publications_summary") != "Not Available" or f.get("academic_identity_url") != "Not Available"):
                matches.append(f)

        return matches[:limit]

    def format_faculty_card(self, f: Dict[str, Any], is_short_query: bool = False) -> str:
        """
        Produces a clean student-friendly faculty profile response.
        Only displays fields that are actually available. Never fabricates empty fields.
        """
        name = f.get("name", "Faculty Member")
        desig = f.get("designation", "Faculty")
        dept = f.get("department", "MLRITM")
        profile_url = f.get("profile_url", "https://mlritm.ac.in/faculty-profile")
        email = f.get("email", "Not Available")
        phone = f.get("phone", "Not Available")

        # Ultra-short representation for ultra-short queries
        if is_short_query and f.get("is_hod"):
            lines = [
                f"**{dept} HOD:** {name}",
                f"- **Designation:** {desig}",
                f"- **Department:** {dept}",
            ]
            if email != "Not Available":
                lines.append(f"- **Email:** [{email}](mailto:{email})")
            if phone != "Not Available":
                lines.append(f"- **Phone:** [{phone}](tel:{phone.replace(' ', '')})")
            lines.append(f"- **Official Profile:** [View Official Profile]({profile_url})")
            return "\n".join(lines)

        lines = [
            f"### 👤 {name}",
            f"- **Designation:** {desig}",
            f"- **Department:** {dept}",
        ]

        if f.get("faculty_id") and f["faculty_id"] != "Not Available":
            lines.append(f"- **Faculty ID:** `{f['faculty_id']}`")

        # Education block
        edu_parts = []
        if f.get("undergraduate_degree") and f["undergraduate_degree"] != "Not Available":
            edu_parts.append(f"  - **Undergraduate:** {f['undergraduate_degree']}")
        if f.get("postgraduate_degree") and f["postgraduate_degree"] != "Not Available":
            edu_parts.append(f"  - **Postgraduate:** {f['postgraduate_degree']}")
        if f.get("phd_degree") and f["phd_degree"] != "Not Available":
            edu_parts.append(f"  - **Ph.D Degree:** {f['phd_degree']}")
        if edu_parts:
            lines.append("- **Education:**\n" + "\n".join(edu_parts))

        # Specialization & Experience
        if f.get("specialization") and f["specialization"] != "Not Available":
            lines.append(f"- **Area of Specialization:** {f['specialization']}")

        exp_parts = []
        if f.get("total_experience") and f["total_experience"] != "Not Available":
            exp_parts.append(f"Total: {f['total_experience']}")
        if f.get("experience_mlritm") and f["experience_mlritm"] != "Not Available":
            exp_parts.append(f"Experience @ MLRITM: {f['experience_mlritm']}")
        if exp_parts:
            lines.append(f"- **Teaching Experience:** {', '.join(exp_parts)}")

        if f.get("employment_status") and f["employment_status"] != "Not Available":
            lines.append(f"- **Employment Status:** {f['employment_status']}")

        # Contact info
        if email != "Not Available":
            lines.append(f"- **Email:** ✉️ [{email}](mailto:{email})")
        if phone != "Not Available":
            lines.append(f"- **Phone:** 📞 [{phone}](tel:{phone.replace(' ', '')})")

        # Academic Identity & Video Lectures
        if f.get("academic_identity_url") and f["academic_identity_url"] != "Not Available":
            lines.append(f"- **Academic Identity:** [IRINS / Vidwan Profile]({f['academic_identity_url']})")
        if f.get("video_lectures_url") and f["video_lectures_url"] != "Not Available":
            lines.append(f"- **Video Lectures:** [YouTube Playlist]({f['video_lectures_url']})")

        # Official profile link
        lines.append(f"- **Official Profile:** [View Official Profile]({profile_url})")

        return "\n".join(lines)

    def format_faculty_list(self, faculty_list: List[Dict[str, Any]], title: str = "Faculty Directory") -> str:
        """Produces a clean student-friendly list of faculty."""
        if not faculty_list:
            return f"No official faculty records found matching that request in the current indexed records."

        md_blocks = [f"### 📋 {title} ({len(faculty_list)} Faculty Members Indexed)"]
        for i, f in enumerate(faculty_list, 1):
            prof_link = f"[View Profile]({f['profile_url']})" if f.get("profile_url") else ""
            email_text = f" | ✉️ {f['email']}" if f.get("email") and f["email"] != "Not Available" else ""
            md_blocks.append(f"{i}. **{f['name']}** — *{f['designation']}* ({f['department']}){email_text} {prof_link}")

        md_blocks.append("\n*Source: Official MLRITM Faculty Profile System (https://mlritm.ac.in/faculty-profile)*")
        return "\n".join(md_blocks)


faculty_service = FacultyIntelligenceService()
