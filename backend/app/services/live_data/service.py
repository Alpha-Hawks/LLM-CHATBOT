"""
Anvaya Live Data Service (Dual-Mode: API vs Deep-Link Fallback).

Handles fetching live student records directly from Anvaya or deep-linking to the portal:
1. PRIMARY (API Mode):
   - Server-side REST API calls to Anvaya / OrgMaker backend.
   - Authenticated with service credentials (api_key or oauth2_client_credentials).
   - Scoped strictly to the student's authenticated roll number.
   - Ephemeral in-memory caching with TTL <= 180s (DPDP Act 2023 compliant).
   - Zero storage on disk, zero logging of personal records.
   - Sandbox / Staging base URL toggle for testing.
   - Graceful fallback on timeout / 404 / 500.

2. FALLBACK (Deep-Link Mode):
   - Active whenever ANVAYA_API_BASE_URL is blank or a feature endpoint is unconfigured.
   - Zero API calls, zero data copied.
   - Replies with direct deep-link to the exact /App portal page for that feature.
"""

import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from pydantic import BaseModel

from backend.app.core.config import settings
from backend.app.services.live_data.cache import ephemeral_live_cache

logger = logging.getLogger(__name__)

# Feature metadata mapping: (Path setting name, Deep-link setting name, Title, Default Portal Path)
FEATURE_REGISTRY: Dict[str, Tuple[str, str, str, str]] = {
    "attendance": (
        "ANVAYA_PATH_ATTENDANCE",
        "ANVAYA_LINK_ATTENDANCE",
        "Student Attendance",
        "https://anvaya.mlritm.ac.in/App/StudentAttendance",
    ),
    "internal_marks": (
        "ANVAYA_PATH_MARKS",
        "ANVAYA_LINK_MARKS",
        "Internal Marks",
        "https://anvaya.mlritm.ac.in/App/InternalMarks",
    ),
    "marks": (
        "ANVAYA_PATH_MARKS",
        "ANVAYA_LINK_MARKS",
        "Internal Marks",
        "https://anvaya.mlritm.ac.in/App/InternalMarks",
    ),
    "results": (
        "ANVAYA_PATH_RESULTS",
        "ANVAYA_LINK_RESULTS",
        "Exam Results",
        "https://anvaya.mlritm.ac.in/App/ExamResults",
    ),
    "fees": (
        "ANVAYA_PATH_FEES",
        "ANVAYA_LINK_FEES",
        "Fee Status & Payments",
        "https://anvaya.mlritm.ac.in/App/FeePayments",
    ),
    "timetable": (
        "ANVAYA_PATH_TIMETABLE",
        "ANVAYA_LINK_TIMETABLE",
        "Class Timetable",
        "https://anvaya.mlritm.ac.in/App/ClassTimetable",
    ),
    "exams": (
        "ANVAYA_PATH_EXAMS",
        "ANVAYA_LINK_EXAMS",
        "Exam Schedule",
        "https://anvaya.mlritm.ac.in/App/ExamSchedule",
    ),
    "profile": (
        "ANVAYA_PATH_PROFILE",
        "ANVAYA_LINK_PROFILE",
        "Student Academic Profile",
        "https://anvaya.mlritm.ac.in/App/StudentProfile",
    ),
}


class LiveDataResult(BaseModel):
    feature: str
    mode: str                           # "api" | "fallback" | "cached"
    status: str                         # "success" | "fallback" | "unavailable" | "error"
    roll_number: str
    deep_link_url: str
    title: str
    answer: str
    raw_data: Optional[Dict[str, Any]] = None
    is_live: bool = False
    source: str = "Anvaya ERP"


class AnvayaLiveDataService:
    """Service orchestrating live student data reads and deep-link fallbacks."""

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._transport = transport
        self._oauth_token: Optional[str] = None
        self._oauth_token_expires_at: float = 0.0

    def get_feature_meta(self, feature: str) -> Tuple[str, str, str, str]:
        canonical = feature.strip().lower()
        if canonical in FEATURE_REGISTRY:
            return FEATURE_REGISTRY[canonical]
        # Default fallback
        return (
            f"ANVAYA_PATH_{canonical.upper()}",
            f"ANVAYA_LINK_{canonical.upper()}",
            canonical.capitalize(),
            f"https://anvaya.mlritm.ac.in/App/{canonical.capitalize()}",
        )

    def get_deep_link(self, feature: str) -> str:
        _, link_attr, _, default_url = self.get_feature_meta(feature)
        return getattr(settings, link_attr, default_url) or default_url

    def is_api_configured_for(self, feature: str) -> bool:
        """True if both the API base URL and the specific feature path are configured."""
        base = settings.effective_anvaya_api_base
        if not base:
            return False
        path_attr, _, _, _ = self.get_feature_meta(feature)
        path = getattr(settings, path_attr, "").strip()
        return bool(path)

    async def _get_oauth_token(self) -> Optional[str]:
        """Fetches or reuses cached OAuth2 Client Credentials access token."""
        now = time.time()
        if self._oauth_token and now < self._oauth_token_expires_at - 30:
            return self._oauth_token

        token_url = getattr(settings, "ANVAYA_OAUTH_TOKEN_URL", "").strip()
        client_id = getattr(settings, "ANVAYA_OAUTH_CLIENT_ID", "").strip()
        client_secret = getattr(settings, "ANVAYA_OAUTH_CLIENT_SECRET", "").strip()

        if not token_url or not client_id or not client_secret:
            logger.error("OAuth2 client credentials incomplete; cannot obtain token.")
            return None

        try:
            async with httpx.AsyncClient(timeout=settings.ANVAYA_API_TIMEOUT_SECONDS, transport=self._transport) as client:
                res = await client.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
                if res.status_code == 200:
                    payload = res.json()
                    self._oauth_token = payload.get("access_token")
                    expires_in = float(payload.get("expires_in", 3600))
                    self._oauth_token_expires_at = now + expires_in
                    return self._oauth_token
                else:
                    logger.error(f"OAuth2 token endpoint returned status {res.status_code}")
                    return None
        except Exception as e:
            logger.error(f"Failed to acquire OAuth2 token: {type(e).__name__}")
            return None

    async def _build_auth_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        auth_method = getattr(settings, "ANVAYA_API_AUTH", "none").strip().lower()

        if auth_method == "api_key":
            key = getattr(settings, "ANVAYA_API_KEY", "").strip()
            header_name = getattr(settings, "ANVAYA_API_KEY_HEADER", "X-API-Key").strip() or "X-API-Key"
            if key:
                headers[header_name] = key
        elif auth_method == "oauth2_client_credentials":
            token = await self._get_oauth_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _format_fallback_response(self, roll_number: str, feature: str) -> LiveDataResult:
        """Generates a deep-link response when no API endpoint is configured."""
        _, _, title, _ = self.get_feature_meta(feature)
        deep_link_url = self.get_deep_link(feature)

        answer = (
            f"Personal academic records are not connected yet via live API. "
            f"To view your current **{title}**, please open the official MLRITM Anvaya portal directly:\n\n"
            f"🔗 **[{title} Portal]({deep_link_url})**\n\n"
            f"*(Your live data is securely rendered directly inside the official Anvaya portal.)*"
        )

        return LiveDataResult(
            feature=feature,
            mode="fallback",
            status="fallback",
            roll_number=roll_number,
            deep_link_url=deep_link_url,
            title=title,
            answer=answer,
            raw_data=None,
            is_live=False,
            source="Anvaya Portal Deep-Link",
        )

    def _format_unavailable_response(
        self,
        roll_number: str,
        feature: str,
        reason: str = "Anvaya's live service is currently not responding",
    ) -> LiveDataResult:
        """Generates a graceful fallback response when the API fails or times out."""
        _, _, title, _ = self.get_feature_meta(feature)
        deep_link_url = self.get_deep_link(feature)

        answer = (
            f"⚠️ **{reason}**.\n\n"
            f"You can view your current live records directly on the Anvaya portal:\n\n"
            f"🔗 **[Open {title} on Anvaya]({deep_link_url})**"
        )

        return LiveDataResult(
            feature=feature,
            mode="api",
            status="unavailable",
            roll_number=roll_number,
            deep_link_url=deep_link_url,
            title=title,
            answer=answer,
            raw_data=None,
            is_live=False,
            source="Anvaya ERP (Offline Fallback)",
        )

    def _render_live_data_markdown(self, feature: str, data: Dict[str, Any], deep_link_url: str) -> str:
        """Renders live JSON data into clean, structured Markdown for the student."""
        _, _, title, _ = self.get_feature_meta(feature)
        lines = [f"### 📋 Live {title} (Anvaya ERP)\n"]

        if feature == "attendance":
            overall = data.get("overall_attendance") or data.get("percentage") or data.get("aggregate_percentage")
            attended = data.get("attended_classes") or data.get("present")
            total = data.get("total_classes") or data.get("total")
            
            if overall is not None:
                lines.append(f"- **Overall Attendance:** **{overall}%**")
            if attended is not None and total is not None:
                lines.append(f"- **Classes Attended:** {attended} / {total}")
            
            subjects = data.get("subjects") or data.get("subject_attendance") or []
            if isinstance(subjects, list) and subjects:
                lines.append("\n**Subject Breakdown:**")
                lines.append("| Subject | Attendance |")
                lines.append("|:---|:---:|")
                for s in subjects:
                    if isinstance(s, dict):
                        s_name = s.get("subject_name") or s.get("name") or s.get("subject_code", "Subject")
                        s_pct = s.get("percentage") or s.get("attendance_percentage", "-")
                        lines.append(f"| {s_name} | {s_pct}% |")

        elif feature in ("internal_marks", "marks"):
            subjects = data.get("subjects") or data.get("internal_marks") or []
            if isinstance(subjects, list) and subjects:
                lines.append("| Subject | Mid-1 | Mid-2 | Assignment | Total |")
                lines.append("|:---|:---:|:---:|:---:|:---:|")
                for s in subjects:
                    if isinstance(s, dict):
                        name = s.get("subject_name") or s.get("name", "-")
                        m1 = s.get("mid1", "-")
                        m2 = s.get("mid2", "-")
                        asn = s.get("assignment", "-")
                        tot = s.get("total", "-")
                        lines.append(f"| {name} | {m1} | {m2} | {asn} | {tot} |")
            else:
                for k, v in data.items():
                    if k not in ("roll_number", "rollNo", "status"):
                        lines.append(f"- **{k.replace('_', ' ').capitalize()}:** {v}")

        elif feature == "results":
            sgpa = data.get("sgpa") or data.get("latest_sgpa")
            cgpa = data.get("cgpa")
            backlogs = data.get("backlogs") or data.get("backlog_count", 0)
            if sgpa is not None:
                lines.append(f"- **Latest SGPA:** **{sgpa}**")
            if cgpa is not None:
                lines.append(f"- **Cumulative CGPA:** **{cgpa}**")
            lines.append(f"- **Active Backlogs:** {backlogs}")

        elif feature == "fees":
            total_fee = data.get("total_fee") or data.get("total_amount")
            paid = data.get("paid_fee") or data.get("paid_amount")
            due = data.get("due_fee") or data.get("balance_due") or data.get("due_amount")
            fee_status = data.get("status") or data.get("payment_status", "Active")
            
            if total_fee is not None:
                lines.append(f"- **Total Fee:** ₹{total_fee:,}" if isinstance(total_fee, (int, float)) else f"- **Total Fee:** {total_fee}")
            if paid is not None:
                lines.append(f"- **Amount Paid:** ₹{paid:,}" if isinstance(paid, (int, float)) else f"- **Amount Paid:** {paid}")
            if due is not None:
                lines.append(f"- **Balance Due:** **₹{due:,}**" if isinstance(due, (int, float)) else f"- **Balance Due:** **{due}**")
            lines.append(f"- **Status:** {fee_status}")

        elif feature == "timetable":
            schedule = data.get("schedule") or data.get("timetable") or []
            if isinstance(schedule, list) and schedule:
                lines.append("| Period | Time | Subject | Room |")
                lines.append("|:---|:---:|:---|:---:|")
                for item in schedule:
                    if isinstance(item, dict):
                        p = item.get("period", "-")
                        t = item.get("time", "-")
                        s = item.get("subject", "-")
                        r = item.get("room", "-")
                        lines.append(f"| {p} | {t} | {s} | {r} |")
            else:
                for k, v in data.items():
                    if k not in ("roll_number", "rollNo"):
                        lines.append(f"- **{k.replace('_', ' ').capitalize()}:** {v}")

        elif feature == "exams":
            exams_list = data.get("exams") or data.get("upcoming_exams") or []
            if isinstance(exams_list, list) and exams_list:
                lines.append("| Subject | Date | Session | Hall |")
                lines.append("|:---|:---:|:---:|:---:|")
                for e in exams_list:
                    if isinstance(e, dict):
                        name = e.get("subject_name") or e.get("subject", "-")
                        dt = e.get("date", "-")
                        sess = e.get("session", "-")
                        hall = e.get("hall", "-")
                        lines.append(f"| {name} | {dt} | {sess} | {hall} |")
            else:
                for k, v in data.items():
                    if k not in ("roll_number", "rollNo"):
                        lines.append(f"- **{k.replace('_', ' ').capitalize()}:** {v}")

        else:
            for k, v in data.items():
                if k not in ("roll_number", "rollNo") and isinstance(v, (str, int, float, bool)):
                    lines.append(f"- **{k.replace('_', ' ').capitalize()}:** {v}")

        lines.append(f"\n🔗 [View live records in Anvaya portal]({deep_link_url})")
        return "\n".join(lines)

    async def get_feature_data(self, roll_number: str, feature: str) -> LiveDataResult:
        """
        Fetches live feature data for the authenticated student.
        - Scoped strictly to student's roll number.
        - Uses ephemeral cache if warm.
        - Falls back gracefully to Anvaya portal deep-link if API is absent or fails.
        """
        clean_roll = str(roll_number).strip().upper()
        clean_feat = str(feature).strip().lower()
        deep_link_url = self.get_deep_link(clean_feat)
        _, _, title, _ = self.get_feature_meta(clean_feat)

        # 1. SPECIAL CASE: Profile Details lookup from official student database
        if clean_feat == "profile":
            from backend.app.services.student_service import getStudentByRollNumberSync
            stored = getStudentByRollNumberSync(clean_roll)
            if isinstance(stored, dict):
                def fv(k):
                    v = stored.get(k)
                    if v is None or str(v).strip() == "" or str(v).lower() in ("none", "null"):
                        return "Not Available"
                    return str(v).strip()

                answer = f"""### 👤 Profile Details

#### 👤 Personal Details
* **Student Name:** {fv('student_name')}
* **Roll Number:** `{fv('roll_number')}`
* **Year of Study:** {fv('year_of_study') or fv('year')}
* **Current Semester:** {fv('semester_display') or fv('current_semester')}
* **Branch:** {fv('branch')}
* **Academic Year:** {fv('academic_year')}
* **Admission Year:** {fv('admission_year') or fv('batch')}
* **Gender:** {fv('gender')}
* **Date of Birth:** {fv('date_of_birth')}

#### 📞 Contact Details
* **Student Mobile:** {fv('student_mobile')}
* **Student Email:** {fv('student_email')}

#### 👨‍👩‍👧 Parent Details
* **Father Mobile:** {fv('father_mobile')}
* **Mother Name:** {fv('mother_name')}
* **Mother Mobile:** {fv('mother_mobile')}
* **Parent Profession:** {fv('parent_profession')}
* **Parent Income:** {fv('parent_income')}
* **Father Name:** {fv('father_name')}

#### 🎓 Admission Details
* **Admission Category:** {fv('admission_category')}
* **Scholarship Type:** {fv('scholarship_type')}
* **Caste Name:** {fv('caste_name')}

#### 📋 Additional Student Details
* **Batch:** {fv('batch')}
* **Section:** {fv('section')}
* **Entry Type:** {fv('entry_type')}"""
                return LiveDataResult(
                    feature="profile",
                    mode="database",
                    status="success",
                    roll_number=clean_roll,
                    deep_link_url=deep_link_url,
                    title="Profile Details",
                    answer=answer,
                    raw_data=stored,
                    is_live=True,
                    source="MLRITM Student Master Database",
                )

        # 2. FALLBACK MODE: Base URL is blank or feature path is unconfigured
        if not self.is_api_configured_for(clean_feat):
            return self._format_fallback_response(clean_roll, clean_feat)

        # 3. CACHE CHECK: Ephemeral in-memory cache
        cached_data = ephemeral_live_cache.get(clean_roll, clean_feat)
        if cached_data is not None:
            formatted_text = self._render_live_data_markdown(clean_feat, cached_data, deep_link_url)
            return LiveDataResult(
                feature=clean_feat,
                mode="cached",
                status="success",
                roll_number=clean_roll,
                deep_link_url=deep_link_url,
                title=title,
                answer=formatted_text,
                raw_data=cached_data,
                is_live=True,
                source="Anvaya Live Cache",
            )

        # 3. API MODE: Call server-side Anvaya API
        base_url = settings.effective_anvaya_api_base
        path_attr, _, _, _ = self.get_feature_meta(clean_feat)
        path_template = getattr(settings, path_attr, "")
        path = path_template.lstrip("/").format(
            roll_number=clean_roll,
            student_key=clean_roll,
            student_id=clean_roll,
        )
        url = f"{base_url}/{path}"
        headers = await self._build_auth_headers()

        try:
            async with httpx.AsyncClient(
                timeout=settings.ANVAYA_API_TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                res = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            logger.warning("Anvaya live API request timed out.")
            return self._format_unavailable_response(
                clean_roll, clean_feat, "Anvaya's live service took longer than expected to respond"
            )
        except httpx.HTTPError as e:
            logger.warning(f"Anvaya live API HTTP error ({type(e).__name__}).")
            return self._format_unavailable_response(clean_roll, clean_feat)

        # Handle HTTP response codes
        if res.status_code == 404:
            return self._format_unavailable_response(
                clean_roll, clean_feat, "No live record was found on Anvaya for your roll number"
            )
        if res.status_code in (401, 403):
            logger.error("Anvaya live API rejected the service credentials.")
            return self._format_unavailable_response(
                clean_roll, clean_feat, "Anvaya live service authorization could not be completed"
            )
        if res.status_code != 200:
            logger.warning(f"Anvaya live API returned status {res.status_code}.")
            return self._format_unavailable_response(clean_roll, clean_feat)

        try:
            payload = res.json()
        except ValueError:
            return self._format_unavailable_response(
                clean_roll, clean_feat, "Anvaya returned a response format that could not be parsed"
            )

        # Extract data payload
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            return self._format_unavailable_response(
                clean_roll, clean_feat, "Anvaya returned an invalid response structure"
            )

        # Security check: verify record belongs to the requested student
        ret_roll = data.get("roll_number") or data.get("rollNo") or data.get("student_id")
        if ret_roll and str(ret_roll).strip().upper() != clean_roll:
            logger.error(f"Security Alert: Anvaya returned mismatched student record. Rejected.")
            return self._format_unavailable_response(
                clean_roll, clean_feat, "Academic record ownership verification failed"
            )

        # Store in ephemeral in-memory cache
        ephemeral_live_cache.set(clean_roll, clean_feat, data)

        formatted_text = self._render_live_data_markdown(clean_feat, data, deep_link_url)
        return LiveDataResult(
            feature=clean_feat,
            mode="api",
            status="success",
            roll_number=clean_roll,
            deep_link_url=deep_link_url,
            title=title,
            answer=formatted_text,
            raw_data=data,
            is_live=True,
            source="Anvaya Live API",
        )


# Global service singleton
live_data_service = AnvayaLiveDataService()
