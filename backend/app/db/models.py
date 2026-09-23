"""
SQLAlchemy Database Models.
Tracks student consent (DPDP Act 2023), academic calendars, and audit logs.
Never stores passwords or live portal session credentials.
"""

from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, Text, UniqueConstraint, ForeignKey, Float
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class StudentConsent(Base):
    __tablename__ = "student_consents"

    id = Column(Integer, primary_key=True, index=True)
    roll_number = Column(String(255), index=True, nullable=False)  # the student's server-side key
    consent_given = Column(Boolean, default=False, nullable=False)
    consent_timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    consent_version = Column(String(20), default="DPDP-2023-v1.0")
    withdrawn = Column(Boolean, default=False, nullable=False)
    withdrawn_timestamp = Column(DateTime, nullable=True)


class AuditLog(Base):
    """
    Audit trail for sensitive operations (sign-in, consent, record access, synchronization).

    `student_ref` is a truncated SHA-256 of the student's server-side key, not the key itself:
    an auditor can follow one student's activity across entries without the log becoming a
    second copy of who the students are. Record contents are never written here.
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    event_type = Column(String(50), nullable=False)  # CHAT_QUERY, AUTH_LAUNCH, CONSENT_GRANT, RECORD_ACCESS, PROFILE_SYNC
    intent_detected = Column(String(50), nullable=True)
    response_status = Column(String(20), default="SUCCESS")
    latency_ms = Column(Integer, default=0)
    student_ref = Column(String(32), index=True, nullable=True)  # pseudonymous, not the student key
    data_category = Column(String(50), nullable=True)            # attendance, marks, results, ...
    detail = Column(String(255), nullable=True)                  # short, non-personal reason


class ChatSession(Base):
    """
    The chatbot's own session, created only after Anvaya's identity assertion is verified.
    Tokens are stored as SHA-256 hashes; the raw values exist only in the student's browser.
    """
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String(64), unique=True, index=True, nullable=True)     # set on handoff exchange
    handoff_hash = Column(String(64), unique=True, index=True, nullable=True)   # one-time code from the redirect
    handoff_expires_at = Column(DateTime, nullable=True)
    identity_provider = Column(String(30), nullable=False)
    issuer = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False, index=True)
    student_key = Column(String(255), nullable=False)       # server-side mapping to the student's records
    roll_number = Column(String(50), nullable=True)
    display_name = Column(String(255), nullable=True)
    anvaya_user_id = Column(String(100), nullable=True)
    student_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    consent_granted_at = Column(DateTime, nullable=True)


class AuthFlowState(Base):
    """Single-use OIDC state (with nonce and PKCE verifier) between the login redirect and the callback."""
    __tablename__ = "auth_flow_states"

    id = Column(Integer, primary_key=True, index=True)
    state_hash = Column(String(64), unique=True, index=True, nullable=False)
    nonce = Column(String(128), nullable=False)
    code_verifier = Column(String(128), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)


class UsedLaunchToken(Base):
    """Replay protection: every launch token ID (jti) is accepted at most once."""
    __tablename__ = "used_launch_tokens"

    id = Column(Integer, primary_key=True, index=True)
    issuer = Column(String(255), nullable=False)
    jti = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("issuer", "jti", name="uq_launch_token_issuer_jti"),)


class AcademicHoliday(Base):
    __tablename__ = "academic_holidays"

    id = Column(Integer, primary_key=True, index=True)
    holiday_date = Column(Date, nullable=False, unique=True)
    holiday_name = Column(String(100), nullable=False)
    holiday_type = Column(String(50), default="National Holiday")
    academic_year = Column(String(20), default="2024-2025")


class StudentProfile(Base):
    """
    Authorized Student Profile Store: one row per student who has signed in through Anvaya.

    Scoped strictly to the authenticated student's unique identity (`student_key`), which the
    backend sets from the verified Anvaya assertion. The columns below exist so the interface can
    show a header ("3rd Year - Semester 5, CSE-A") without decrypting anything; the full academic
    snapshot lives in `profile_data_json`, encrypted with AES-256-GCM.

    Every column that Anvaya may not supply is nullable: the assistant reports "not available"
    rather than defaulting a real student to an invented semester or academic year.
    """

    __tablename__ = "student_profiles"

    id = Column(Integer, primary_key=True, index=True)
    student_key = Column(String(255), unique=True, index=True, nullable=False)
    roll_number = Column(String(50), index=True, nullable=True)
    anvaya_user_id = Column(String(100), index=True, nullable=True)
    student_id = Column(String(100), index=True, nullable=True)
    display_name = Column(String(255), nullable=True)
    department = Column(String(100), nullable=True)
    course = Column(String(50), nullable=True)
    year = Column(String(20), nullable=True)
    current_semester = Column(Integer, nullable=True)
    section = Column(String(10), nullable=True)
    academic_year = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    profile_data_json = Column(Text, nullable=False)   # AES-256-GCM ciphertext of the snapshot
    provider = Column(String(30), nullable=True)       # the data provider that produced it
    last_synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    source = Column(String(100), default="Anvaya ERP")


class Student(Base):
    """
    Official MLRITM Student Master Record.
    Derived from official college PDF rolls with zero data loss.
    The uppercased and trimmed roll_number is the unique primary key.
    """
    __tablename__ = "students"

    roll_number = Column(String(20), primary_key=True, index=True)
    student_name = Column(String(255), nullable=False)
    name = Column(String(255), nullable=True)            # compatibility alias for auth queries
    batch = Column(String(10), nullable=True)
    admission_batch = Column(String(10), nullable=True)  # compatibility alias for auth queries
    entry_type = Column(String(20), nullable=True)       # 'Regular (1A)' or 'Lateral Entry (5A)'
    year = Column(String(20), nullable=True)             # derived study year / admission year
    section = Column(String(10), nullable=True)
    academic_year = Column(String(50), nullable=True)
    gender = Column(String(20), nullable=True)
    date_of_birth = Column(String(20), nullable=True)
    student_mobile = Column(String(30), nullable=True)
    student_email = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)           # compatibility alias for auth queries
    father_name = Column(String(255), nullable=True)
    father_mobile = Column(String(30), nullable=True)
    mother_name = Column(String(255), nullable=True)
    mother_mobile = Column(String(30), nullable=True)
    branch = Column(String(100), nullable=True)
    admission_year = Column(String(20), nullable=True)
    scholarship_type = Column(String(100), nullable=True)
    parent_income = Column(String(50), nullable=True)
    parent_profession = Column(String(100), nullable=True)
    admission_category = Column(String(50), nullable=True)
    caste_name = Column(String(100), nullable=True)
    current_semester = Column(String(50), nullable=True)

    # Provenance and verification metadata
    source_file = Column(String(255), nullable=True)
    source_page = Column(Integer, nullable=True)
    source_row = Column(Integer, nullable=True)
    extraction_method = Column(String(50), nullable=True)
    needs_review = Column(Boolean, default=False, nullable=False)
    review_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CollegeSource(Base):
    """Authoritative MLRITM web sources tracked with content hashes and sync dates."""
    __tablename__ = "college_sources"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(500), unique=True, index=True, nullable=False)
    title = Column(String(255), nullable=False)
    category = Column(String(100), nullable=False)  # phone_directory, events, media, calendar, regulations
    content_hash = Column(String(64), nullable=True)
    http_status = Column(Integer, default=200)
    last_synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FacultyContact(Base):
    """
    Authoritative MLRITM Faculty Intelligence & Directory Model.
    Ingested directly from official MLRITM Faculty Profile system: https://mlritm.ac.in/faculty-profile
    and Phone Directory: https://www.mlritm.ac.in/Phone_Directory.
    """
    __tablename__ = "faculty_directory"

    id = Column(Integer, primary_key=True, index=True)
    faculty_id = Column(String(50), nullable=True, index=True)  # e.g., MLRS10003
    name = Column(String(255), nullable=False, index=True)
    normalized_name = Column(String(255), nullable=True, index=True)
    designation = Column(String(255), nullable=False)
    department = Column(String(100), nullable=True, index=True)
    department_code = Column(String(50), nullable=True, index=True)  # e.g., CSE, IT, CSE-AI-ML, ECE
    is_hod = Column(Boolean, default=False, nullable=False, index=True)
    responsibility = Column(String(255), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    phone = Column(String(50), nullable=True)

    # Detailed Academic Profile Fields
    profile_url = Column(String(500), nullable=True)
    photo_url = Column(String(500), nullable=True)
    total_experience = Column(String(100), default="Not Available")
    experience_mlritm = Column(String(100), default="Not Available")
    undergraduate_degree = Column(String(255), default="Not Available")
    postgraduate_degree = Column(String(255), default="Not Available")
    phd_degree = Column(String(255), default="Not Available")
    employment_status = Column(String(50), default="Full-Time")
    specialization = Column(String(255), default="Not Available", index=True)
    academic_identity_url = Column(String(500), default="Not Available")
    video_lectures_url = Column(String(500), default="Not Available")
    courses_taught = Column(Text, default="Not Available")
    research_interests = Column(Text, default="Not Available")
    publications_summary = Column(Text, default="Not Available")
    patents_info = Column(Text, default="Not Available")
    status = Column(String(50), default="ACTIVE", index=True)  # ACTIVE, CHANGED, HISTORICAL

    source_url = Column(String(500), default="https://mlritm.ac.in/faculty-profile")
    last_synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CollegeEvent(Base):
    """
    Authoritative MLRITM College Events.
    Ingested from https://www.mlritm.ac.in/upcoming-and-ongoing-events.
    """
    __tablename__ = "college_events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), unique=True, index=True, nullable=False)
    event_date = Column(Date, nullable=True, index=True)
    date_display = Column(String(100), nullable=True)
    start_time = Column(String(50), nullable=True)
    end_time = Column(String(50), nullable=True)
    venue = Column(String(255), default="MLRITM Campus, Dundigal")
    organizer = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    category = Column(String(50), default="event")  # fest, sports, workshop, hackathon, seminar, etc.
    status = Column(String(50), default="PAST")     # UPCOMING, ONGOING, PAST, DATE_UNKNOWN
    registration_url = Column(String(500), nullable=True)
    source_url = Column(String(500), default="https://www.mlritm.ac.in/upcoming-and-ongoing-events")
    poster_url = Column(String(500), nullable=True)
    last_synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EventMedia(Base):
    """
    Official event photos, posters, and gallery media.
    Ingested from https://www.mlritm.ac.in/mlritm-media.
    """
    __tablename__ = "event_media"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("college_events.id", ondelete="CASCADE"), nullable=True, index=True)
    media_type = Column(String(50), default="photo")  # photo, poster, news_clipping
    url = Column(String(500), nullable=False)
    caption = Column(String(255), nullable=True)
    alt_text = Column(String(255), nullable=True)


class CollegeNotice(Base):
    """
    Official MLRITM Circulars, Notices, and Announcements.
    """
    __tablename__ = "college_notices"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    publish_date = Column(Date, nullable=True)
    category = Column(String(100), default="General")
    url = Column(String(500), nullable=True)
    file_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    source_url = Column(String(500), default="https://www.mlritm.ac.in")
    last_synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
 
 
class KnowledgeGap(Base):
    """
    Dedicated MLRITM Knowledge-Gap Tracking System.
    Tracks unresolved, unusual, or recurring student questions to drive continuous learning.
    Never stores sensitive personal student information.
    """
    __tablename__ = "knowledge_gaps"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(Text, nullable=False)
    normalized_question = Column(String(500), index=True, nullable=False)
    intent = Column(String(50), default="UNKNOWN")
    topic = Column(String(100), default="general", index=True)
    department = Column(String(100), nullable=True, index=True)
    frequency = Column(Integer, default=1, nullable=False)
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    answer_status = Column(String(50), default="NEW", nullable=False, index=True)
    # Possible statuses: NEW, SEARCHING, ANSWERED, VERIFIED, UNVERIFIED, NO_OFFICIAL_SOURCE, NEEDS_REVIEW, STALE, REJECTED
    source_found = Column(String(255), nullable=True)
    source_url = Column(String(500), nullable=True)
    verification_status = Column(String(50), default="UNVERIFIED", nullable=False, index=True)
    # Possible: VERIFIED, UNVERIFIED, REJECTED
    confidence = Column(Float, default=0.0)
    resolved_at = Column(DateTime, nullable=True)
    next_verification_at = Column(DateTime, nullable=True)


class AdaptiveKnowledgeItem(Base):
    """
    Verified Adaptive Knowledge Repository.
    Stores authoritative facts learned through official MLRITM sources or verified by admin.
    Full provenance, versioning, temporal awareness, and content hashes.
    """
    __tablename__ = "adaptive_knowledge_items"

    id = Column(Integer, primary_key=True, index=True)
    topic = Column(String(100), index=True, nullable=False)
    question_pattern = Column(String(500), nullable=False)
    normalized_question = Column(String(500), index=True, nullable=False)
    verified_content = Column(Text, nullable=False)
    source_url = Column(String(500), nullable=False)
    source_trust_level = Column(String(50), default="OFFICIAL_MLRITM", nullable=False)
    # Trust levels: OFFICIAL_MLRITM, OFFICIAL_ANVAYA, OFFICIAL_DOCUMENT, AUTHORIZED_SOURCE, UNVERIFIED
    content_hash = Column(String(64), nullable=False)
    version = Column(Integer, default=1, nullable=False)
    is_current = Column(Boolean, default=True, index=True, nullable=False)
    temporal_status = Column(String(50), default="CURRENT", nullable=False)
    # Temporal: CURRENT, UPCOMING, ONGOING, PAST, HISTORICAL, DATE_UNKNOWN
    department = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    retrieved_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    verified_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    next_verification_at = Column(DateTime, nullable=True)


class UserFeedback(Base):
    """
    User Feedback Loop (Helpful / Not Helpful).
    Collects student signals on missing or incomplete information without treating feedback as factual truth.
    """
    __tablename__ = "user_feedback"

    id = Column(Integer, primary_key=True, index=True)
    query = Column(Text, nullable=False)
    feedback_type = Column(String(20), nullable=False)  # HELPFUL, NOT_HELPFUL
    missing_info = Column(Text, nullable=True)
    session_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FacultySyncLog(Base):
    """
    Tracks faculty synchronization runs, detecting changes, additions, and updates.
    """
    __tablename__ = "faculty_sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    sync_timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String(50), default="SUCCESS")
    total_scanned = Column(Integer, default=0)
    faculty_added = Column(Integer, default=0)
    faculty_updated = Column(Integer, default=0)
    faculty_removed = Column(Integer, default=0)
    changes_json = Column(Text, nullable=True)
    source_url = Column(String(500), default="https://mlritm.ac.in/faculty-profile")
    error_details = Column(Text, nullable=True)


class QueryPatternLog(Base):
    """
    Query Learning & Pattern Store for Short Queries & Intent Resolution.
    Tracks anonymized student expressions, typos, confidence, and intent mappings.
    Never stores sensitive personal student data.
    """
    __tablename__ = "query_pattern_logs"

    id = Column(Integer, primary_key=True, index=True)
    raw_query = Column(String(500), nullable=False)
    normalized_query = Column(String(500), nullable=False, index=True)
    predicted_intent = Column(String(50), nullable=False, index=True)
    resolved_intent = Column(String(50), nullable=False, index=True)
    entities_json = Column(Text, nullable=True)
    confidence = Column(Float, default=1.0)
    source_used = Column(String(100), default="MLRITM Faculty Intelligence")
    success = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DepartmentAlias(Base):
    """
    Configurable Department Alias Registry.
    Maps informal aliases (e.g. 'cse', 'aiml', 'ds', 'mech') to canonical department codes and names.
    """
    __tablename__ = "department_aliases"

    id = Column(Integer, primary_key=True, index=True)
    alias = Column(String(100), unique=True, index=True, nullable=False)
    canonical_code = Column(String(50), nullable=False, index=True)
    canonical_name = Column(String(100), nullable=False)
    is_active = Column(Boolean, default=True)




