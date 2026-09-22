"""
SQLAlchemy Database Models.
Tracks student consent (DPDP Act 2023), academic calendars, and audit logs.
Never stores passwords or live portal session credentials.
"""

from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, Text, UniqueConstraint
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

