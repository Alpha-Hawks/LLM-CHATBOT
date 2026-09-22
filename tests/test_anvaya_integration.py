"""
Tests for the pieces that carry real student data:

    * the official-API provider (mocked transport - no network, no real credentials)
    * encryption at rest in the profile store
    * the audit trail
    * transport security and the weak-secret startup guard
"""

import asyncio
import json
from datetime import datetime

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from backend.app.core import security
from backend.app.core.config import settings
from backend.app.db.models import AuditLog, ChatSession, StudentProfile
from backend.app.db.session import AsyncSessionLocal
from backend.app.main import app
from backend.app.services import audit, student_profile_store
from backend.app.services.student_data import providers
from backend.app.services.student_data.base import StudentDataNotConfigured, StudentDataUnavailable
from tests.auth_test_utils import bearer, configured, sign_in, student_context

API_SETTINGS = dict(
    ANVAYA_API_BASE_URL="https://api.anvaya.example.test",
    ANVAYA_API_STUDENT_PATH="/v1/students/{student_key}/academic",
    ANVAYA_API_AUTH_SCHEME="bearer",
    ANVAYA_API_TOKEN="service-credential-not-a-student-password",
    ANVAYA_API_TIMEOUT_SECONDS=5.0,
)

OFFICIAL_RESPONSE = {
    "data": {
        "rollNo": "23R21A1234",
        "name": "Official Name",
        "branch": "Computer Science & Engineering (AI & ML)",
        "program": "B.Tech",
        "year": "3rd Year",
        "semester": "6",
        "section": "C",
        "academicYear": "2025-2026",
        "subjects": [
            {"subject_code": "AM601PC", "subject_name": "Deep Learning", "subject_type": "Theory",
             "credits": 3, "semester": 6, "faculty_name": "Dr. Official"},
            {"subject_code": "not-a-valid-row"},          # malformed: must be skipped, not crash
        ],
        "faculty": [
            {"name": "Dr. Official", "subject_code": "AM601PC", "subject_name": "Deep Learning", "semester": 6},
        ],
    }
}


def _run(coro):
    return asyncio.run(coro)


def _provider(handler):
    return providers.AnvayaApiStudentDataProvider(transport=httpx.MockTransport(handler))


# =============================================================================================
# Official API provider
# =============================================================================================

def test_official_provider_is_off_until_fully_configured():
    provider = providers.AnvayaApiStudentDataProvider()
    with configured(**{**API_SETTINGS, "ANVAYA_API_BASE_URL": ""}):
        assert provider.is_configured() is False
    with configured(**{**API_SETTINGS, "ANVAYA_API_STUDENT_PATH": "/v1/students/all"}):   # no {student_key}
        assert provider.is_configured() is False
    with configured(**{**API_SETTINGS, "ANVAYA_API_TOKEN": ""}):
        assert provider.is_configured() is False
    with configured(**{**API_SETTINGS, "ANVAYA_API_BASE_URL": "http://insecure.example.test", "ENVIRONMENT": "production"}):
        assert provider.is_configured() is False
    with configured(**API_SETTINGS):
        assert provider.is_configured() is True


def test_selecting_the_official_provider_fails_closed_when_misconfigured():
    with configured(STUDENT_DATA_PROVIDER="anvaya_api", ANVAYA_API_BASE_URL=""):
        assert providers.get_student_data_provider().name == "none"
    with configured(STUDENT_DATA_PROVIDER="anvaya_api", **API_SETTINGS):
        assert providers.get_student_data_provider().name == "anvaya_api"
    with configured(STUDENT_DATA_PROVIDER="typo-provider"):
        assert providers.get_student_data_provider().name == "none"


def test_official_provider_requests_only_the_signed_in_students_record():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=OFFICIAL_RESPONSE)

    with configured(**API_SETTINGS):
        student = student_context("anvaya-uid-77", "23R21A1234")
        _run(_provider(handler).get_profile(student))

    assert len(seen) == 1
    assert seen[0].url.path == "/v1/students/anvaya-uid-77/academic"
    assert seen[0].headers["authorization"] == "Bearer service-credential-not-a-student-password"
    # Nothing about the request carries a password, a roll number query or a second student
    assert "password" not in str(seen[0].url).lower() and not seen[0].url.query


def test_official_provider_maps_the_response_and_skips_malformed_rows():
    with configured(**API_SETTINGS):
        profile = _run(_provider(lambda r: httpx.Response(200, json=OFFICIAL_RESPONSE)).get_profile(
            student_context("uid-1", "23R21A1234")))

    assert profile.department == "Computer Science & Engineering (AI & ML)"
    assert profile.current_semester == 6
    assert profile.section == "C" and profile.academic_year == "2025-2026"
    assert [s.subject_name for s in profile.current_subjects] == ["Deep Learning"]   # malformed row dropped
    assert profile.email is None                                                     # not authorized -> not invented
    assert profile.attendance is None and profile.timetable is None                  # not supplied -> absent, not faked


def test_official_provider_takes_ownership_from_the_session_not_the_payload():
    tampered = {"data": {**OFFICIAL_RESPONSE["data"], "student_key": "someone-else", "rollNo": "VICTIM"}}
    with configured(**API_SETTINGS):
        profile = _run(_provider(lambda r: httpx.Response(200, json=tampered)).get_profile(
            student_context("uid-real", "23R21A1234")))
    assert profile.student_key == "uid-real"


def test_official_provider_reports_failures_without_inventing_data():
    student = student_context("uid-1", "R1")
    cases = {
        "not_found": (lambda r: httpx.Response(404), "no academic record"),
        "refused": (lambda r: httpx.Response(403), "not currently authorized"),
        "server_error": (lambda r: httpx.Response(503), "unexpected response"),
        "bad_json": (lambda r: httpx.Response(200, content=b"<html>login</html>"), "could not read"),
        "not_an_object": (lambda r: httpx.Response(200, json=["list"]), "could not read"),
    }
    with configured(**API_SETTINGS):
        for name, (handler, phrase) in cases.items():
            try:
                _run(_provider(handler).get_profile(student))
                raise AssertionError(f"{name}: expected StudentDataUnavailable")
            except StudentDataUnavailable as e:
                assert phrase in str(e), f"{name}: {e}"

        def unreachable(request):
            raise httpx.ConnectTimeout("timeout", request=request)

        try:
            _run(_provider(unreachable).get_profile(student))
            raise AssertionError("expected StudentDataUnavailable")
        except StudentDataUnavailable as e:
            assert "not responding" in str(e)


def test_official_provider_refuses_to_run_unconfigured():
    with configured(**{**API_SETTINGS, "ANVAYA_API_BASE_URL": ""}):
        try:
            _run(providers.AnvayaApiStudentDataProvider().get_profile(student_context()))
            raise AssertionError("expected StudentDataNotConfigured")
        except StudentDataNotConfigured:
            pass


def test_chat_serves_official_data_end_to_end_with_the_service_credential_never_leaking():
    provider = providers.AnvayaApiStudentDataProvider(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=OFFICIAL_RESPONSE))
    )
    original = providers._anvaya_api
    providers._anvaya_api = provider
    try:
        with configured(STUDENT_DATA_PROVIDER="anvaya_api", **API_SETTINGS), TestClient(app) as c:
            token = sign_in(c, sub="uid-e2e", roll="23R21A1234")
            body = c.post(
                "/api/v1/chat/", json={"message": "Which subjects am I studying this semester?"}, headers=bearer(token)
            ).json()

            assert "Deep Learning" in body["answer"] and "Semester 6" in body["answer"]
            assert "Sample data" not in body["answer"]                       # not the development set
            assert "service-credential" not in json.dumps(body)              # the API token never reaches a student
            assert body["current_semester"] == 6
    finally:
        providers._anvaya_api = original


# =============================================================================================
# Encryption at rest
# =============================================================================================

def _stored_row(student_key):
    async def run():
        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(StudentProfile).where(StudentProfile.student_key == student_key)
            )).scalar_one_or_none()
    return _run(run())


def test_stored_snapshot_is_ciphertext_not_readable_records():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="enc-1", roll="237Y1A1270", name="Encrypted Person")
        c.get("/api/v1/student/profile", headers=bearer(token))

        row = _stored_row("enc-1")
        assert row is not None
        blob = row.profile_data_json
        for plaintext in ("Database Management Systems", "72.08", "Dr. S. Sharma", "attendance", "Encrypted Person"):
            assert plaintext not in blob, f"{plaintext!r} is readable in the stored snapshot"


def test_snapshot_written_with_another_key_is_discarded_not_shown():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="enc-2", roll="237Y1A1270")
        c.get("/api/v1/student/profile", headers=bearer(token))

        original = settings.AES_ENCRYPTION_KEY
        settings.AES_ENCRYPTION_KEY = "ffffffffffffffffffffffffffffffff"
        try:
            stored = _run(_load_stored(student_context("enc-2", "237Y1A1270")))
        finally:
            settings.AES_ENCRYPTION_KEY = original
        assert stored is None       # undecryptable -> treated as absent, never returned


async def _load_stored(student):
    async with AsyncSessionLocal() as db:
        return await student_profile_store.load(db, student)


def test_store_refuses_to_persist_another_students_profile():
    async def attempt():
        async with AsyncSessionLocal() as db:
            profile = await providers.SampleStudentDataProvider().get_profile(student_context("owner-a", "R-A"))
            await student_profile_store.save(db, student_context("owner-b", "R-B"), profile)

    with configured():
        try:
            _run(attempt())
            raise AssertionError("a profile for another student was persisted")
        except PermissionError:
            pass
    assert _stored_row("owner-b") is None


def test_store_never_returns_a_row_belonging_to_another_key():
    """Even if a row's ciphertext is moved under someone else's key, it is refused."""
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="row-a", roll="237Y1A1270")
        c.get("/api/v1/student/profile", headers=bearer(token))
        blob = _stored_row("row-a").profile_data_json

        async def graft():
            # The suite shares one database, so start from a clean slate and leave one behind
            async with AsyncSessionLocal() as db:
                await db.execute(delete(StudentProfile).where(StudentProfile.student_key == "row-b"))
                db.add(StudentProfile(student_key="row-b", profile_data_json=blob))
                await db.commit()
            try:
                return await _load_stored(student_context("row-b", "R-B"))
            finally:
                async with AsyncSessionLocal() as db:
                    await db.execute(delete(StudentProfile).where(StudentProfile.student_key == "row-b"))
                    await db.commit()

        assert _run(graft()) is None


# =============================================================================================
# Audit trail
# =============================================================================================

def _audit_rows(event_type=None):
    async def run():
        async with AsyncSessionLocal() as db:
            query = select(AuditLog)
            if event_type:
                query = query.where(AuditLog.event_type == event_type)
            return (await db.execute(query)).scalars().all()
    return _run(run())


def test_record_access_is_audited_without_recording_the_data():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="audit-1", roll="237Y1A1270", name="Audited Person")
        c.post("/api/v1/chat/", json={"message": "What is my attendance?"}, headers=bearer(token))

        ref = audit.student_ref(student_context("audit-1"))
        mine = [r for r in _audit_rows("RECORD_ACCESS") if r.student_ref == ref]
        assert mine, "no RECORD_ACCESS entry was written"
        assert mine[-1].data_category == "attendance"

        everything = " ".join(str(v) for r in _audit_rows() for v in (r.detail, r.data_category, r.student_ref, r.intent_detected))
        for sensitive in ("72.08", "Audited Person", "237Y1A1270", "audit-1", "What is my attendance"):
            assert sensitive not in everything, f"{sensitive!r} was written to the audit log"


def test_audit_reference_is_a_hash_not_the_student_key():
    ref = audit.student_ref(student_context("plain-key-123"))
    assert ref != "plain-key-123" and "plain-key" not in ref
    assert len(ref) == 32 and ref == audit.student_ref(student_context("plain-key-123"))   # stable
    assert ref != audit.student_ref(student_context("plain-key-124"))
    assert audit.student_ref(None) is None


def test_sync_and_erasure_are_audited():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="audit-2", roll="237Y1A1270")
        c.post("/api/v1/student/sync", headers=bearer(token))
        c.delete("/api/v1/student/data", headers=bearer(token))

        ref = audit.student_ref(student_context("audit-2"))
        events = {r.event_type for r in _audit_rows() if r.student_ref == ref}
        assert {"PROFILE_SYNC", "PROFILE_ERASE"} <= events


def test_audit_failure_never_breaks_the_answer():
    class BrokenDb:
        def add(self, *_):
            raise RuntimeError("audit store down")

    _run(audit.record(BrokenDb(), "RECORD_ACCESS", student=student_context()))   # must not raise


# =============================================================================================
# Session lifetime
# =============================================================================================

def test_expired_session_is_refused_everywhere():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="expiry-1", roll="237Y1A1270")
        assert c.get("/api/v1/student/profile", headers=bearer(token)).status_code == 200

        async def expire():
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(ChatSession).where(ChatSession.subject == "expiry-1").values(expires_at=datetime(2000, 1, 1))
                )
                await db.commit()

        _run(expire())

        assert c.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401
        assert c.get("/api/v1/student/profile", headers=bearer(token)).status_code == 401
        data = c.post("/api/v1/chat/", json={"message": "What is my attendance?"}, headers=bearer(token)).json()
        assert data["requires_auth"] is True and "Attendance Report" not in data["answer"]


# =============================================================================================
# Transport security
# =============================================================================================

def test_api_responses_are_never_cacheable_and_carry_security_headers():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="hdr-1", roll="237Y1A1270")
        res = c.get("/api/v1/student/profile", headers=bearer(token))

        assert res.headers["cache-control"] == "no-store"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'self' https://anvaya.mlritm.ac.in" in res.headers["content-security-policy"]


def test_streamed_chat_answers_are_also_not_cacheable():
    with configured(), TestClient(app) as c:
        res = c.post("/api/v1/chat/stream", json={"message": "Hello"})
        assert res.headers["cache-control"] == "no-store"
        assert "[DONE]" in res.text          # the middleware did not break the stream


def test_https_is_enforced_when_switched_on_except_for_health_probes():
    with configured(ENFORCE_HTTPS=True), TestClient(app, follow_redirects=False) as c:
        redirect = c.get("/api/v1/auth/config")
        assert redirect.status_code == 308
        assert redirect.headers["location"].startswith("https://")

        assert c.get("/api/v1/health/live").status_code == 200          # probes stay reachable

        behind_proxy = c.get("/api/v1/auth/config", headers={"x-forwarded-proto": "https"})
        assert behind_proxy.status_code == 200


def test_hsts_is_sent_on_https_in_production_only():
    with configured(ENVIRONMENT="production", STUDENT_DATA_PROVIDER="none"), TestClient(app) as c:
        secure = c.get("/api/v1/health/live", headers={"x-forwarded-proto": "https"})
        assert "max-age=" in secure.headers.get("strict-transport-security", "")
        assert "strict-transport-security" not in c.get("/api/v1/health/live").headers   # plain HTTP: no HSTS

    with configured(ENVIRONMENT="development"), TestClient(app) as c:
        assert "strict-transport-security" not in c.get(
            "/api/v1/health/live", headers={"x-forwarded-proto": "https"}
        ).headers


# =============================================================================================
# Weak-secret guard
# =============================================================================================

def test_real_data_provider_refuses_to_start_on_the_public_placeholder_key():
    with configured(ENVIRONMENT="production", STUDENT_DATA_PROVIDER="anvaya_api", **API_SETTINGS,
                    AES_ENCRYPTION_KEY="0123456789abcdef0123456789abcdef"):
        try:
            security.assert_secrets_fit_for_student_data()
            raise AssertionError("started with the placeholder AES key")
        except RuntimeError as e:
            assert "AES_ENCRYPTION_KEY" in str(e)


def test_guard_allows_strong_secrets_development_and_no_data_provider():
    strong = dict(AES_ENCRYPTION_KEY="9f2c41d7a3b85e60c1d4f7a92b3e8c05", SECRET_KEY="a-long-random-value-generated-for-this-deployment")
    with configured(ENVIRONMENT="production", STUDENT_DATA_PROVIDER="anvaya_api", **API_SETTINGS, **strong):
        security.assert_secrets_fit_for_student_data()

    with configured(ENVIRONMENT="development", STUDENT_DATA_PROVIDER="sample"):
        security.assert_secrets_fit_for_student_data()          # development may use placeholders

    with configured(ENVIRONMENT="production", STUDENT_DATA_PROVIDER="none"):
        security.assert_secrets_fit_for_student_data()          # nothing sensitive is stored

    # A provider that resolves to "none" (misconfigured, or sample outside development) stores nothing
    with configured(ENVIRONMENT="production", STUDENT_DATA_PROVIDER="anvaya_api", ANVAYA_API_BASE_URL=""):
        security.assert_secrets_fit_for_student_data()


def test_short_aes_key_is_reported():
    with configured(AES_ENCRYPTION_KEY="short"):
        assert any("shorter than 32" in p for p in security.weak_secret_problems())


def test_production_sign_in_button_tells_the_student_to_start_from_anvaya():
    """With signed launch in production only Anvaya can start the sign-in, and the client is told so."""
    with configured(ENVIRONMENT="production", STUDENT_DATA_PROVIDER="none"), TestClient(app) as c:
        cfg = c.get("/api/v1/auth/config").json()
        assert cfg["sign_in_mode"] == "anvaya_launch" and cfg["sign_in_url"] is None

    with configured(IDENTITY_PROVIDER="none"), TestClient(app) as c:
        assert c.get("/api/v1/auth/config").json()["sign_in_mode"] == "unavailable"

    with configured(), TestClient(app) as c:
        assert c.get("/api/v1/auth/config").json()["sign_in_mode"] == "dev_simulator"
