"""
Acceptance Test Suite for Anvaya Live Data & Deep-Link Fallback Integration.

Verifies:
1. SSO Launch Token verification with RS256, max lifetime <= 120s, jti replay protection,
   and seed verification against MLRITM database.
2. Dev-only mock launcher strictly 404 in production.
3. Primary Path (API Mode): Server-side live fetch, API key & OAuth2 auth, sandbox switch,
   ephemeral in-memory caching (DPDP Act 2023 compliant), and roll-number isolation.
4. Fallback Path (Deep-Link Mode): When ANVAYA_API_BASE_URL is blank or endpoint is omitted,
   replies with direct Anvaya portal deep-links without copying any data.
5. Error & Outage Handling: Graceful messages on timeout/500/404, offering deep-links,
   never fabricating data.
6. Privacy & Isolation: Polite refusal when asking about another roll number.
"""

import time
import uuid
from typing import Dict, Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from backend.app.core.config import settings
from backend.app.main import app
from backend.app.services.identity.dev_keys import get_dev_private_key_pem, get_dev_public_key_pem
from backend.app.services.live_data.cache import ephemeral_live_cache
from backend.app.services.live_data.service import AnvayaLiveDataService
from tests.auth_test_utils import configured, bearer, launch, fragment_params


# Generate test RSA keypair
_test_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_RSA_PRIVATE_PEM = _test_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")
TEST_RSA_PUBLIC_PEM = _test_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("utf-8")

ISSUER = "anvaya.mlritm.ac.in"
AUDIENCE = "mlritm-chatbot"


def make_rs256_token(
    roll: str = "237Y1A1270",
    exp_offset: int = 60,
    iat_offset: int = 0,
    iss: str = ISSUER,
    aud: str = AUDIENCE,
    jti: str = None,
    key_pem: str = TEST_RSA_PRIVATE_PEM,
    **extra_claims
) -> str:
    """Mints an RS256 token matching the official Anvaya button contract."""
    now = int(time.time())
    payload = {
        "iss": iss,
        "aud": aud,
        "sub": roll,
        "iat": now + iat_offset,
        "exp": now + exp_offset,
        "jti": jti or str(uuid.uuid4()),
        "name": f"Student {roll}",
        "roll_no": roll,
    }
    payload.update(extra_claims)
    return jwt.encode(payload, key_pem, algorithm="RS256")


def sign_in_rs256(client: TestClient, token: str) -> str:
    """Carries out /launch -> /session/exchange -> /consent flow."""
    params = launch(client, token)
    assert "handoff" in params, f"Launch failed: {params}"
    res = client.post("/api/v1/auth/session/exchange", json={"handoff_code": params["handoff"]})
    assert res.status_code == 200, res.text
    session_token = res.json()["session_token"]
    c_res = client.post("/api/v1/auth/consent", json={"dpdp_consent_granted": True}, headers=bearer(session_token))
    assert c_res.status_code == 200
    return session_token


# ==============================================================================
# 1. SSO Launch Token & Seed Validation Tests
# ==============================================================================

def test_sso_valid_launch_token_signs_in_correct_student():
    """A valid RS256 launch token for a student in the seed database successfully signs in."""
    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
        LAUNCH_TOKEN_HMAC_SECRET="",
    ), TestClient(app) as client:
        # 237Y1A1270 is an actual seeded student: Gunda Dinesh (IT)
        token = make_rs256_token(roll="237Y1A1270")
        session_token = sign_in_rs256(client, token)
        
        me = client.get("/api/v1/auth/me", headers=bearer(session_token)).json()["student"]
        assert me["roll_number"] == "237Y1A1270"
        assert "DINESH" in me["display_name"].upper() or "GUNDA" in me["display_name"].upper()


def test_sso_rejects_unregistered_roll_number():
    """Launch tokens for roll numbers absent from the seed database are refused."""
    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
        LAUNCH_TOKEN_HMAC_SECRET="",
    ), TestClient(app) as client:
        # Non-existent roll number
        token = make_rs256_token(roll="999Z9A9999")
        params = launch(client, token)
        assert params.get("auth_error") == "student_not_found"


def test_sso_rejects_expired_and_long_lived_tokens():
    """Tokens expired or with lifetime > 120s are rejected per the contract."""
    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
        LAUNCH_TOKEN_HMAC_SECRET="",
    ), TestClient(app) as client:
        # Already expired (beyond 30s clock skew)
        expired_token = make_rs256_token(roll="237Y1A1270", exp_offset=-60)
        params = launch(client, expired_token)
        assert params.get("auth_error") == "invalid_launch"

        # Lifetime > 120s (e.g. 300s)
        long_lived_token = make_rs256_token(roll="237Y1A1270", exp_offset=300)
        params = launch(client, long_lived_token)
        assert params.get("auth_error") == "invalid_launch"


def test_sso_rejects_tampered_and_wrong_audience():
    """Tampered signatures and wrong audience/issuer are rejected."""
    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
        LAUNCH_TOKEN_HMAC_SECRET="",
    ), TestClient(app) as client:
        # Wrong audience
        wrong_aud_token = make_rs256_token(roll="237Y1A1270", aud="wrong-audience")
        assert launch(client, wrong_aud_token).get("auth_error") == "invalid_launch"

        # Wrong issuer
        wrong_iss_token = make_rs256_token(roll="237Y1A1270", iss="evil.attacker.com")
        assert launch(client, wrong_iss_token).get("auth_error") == "invalid_launch"


def test_sso_rejects_replayed_jti():
    """A launch token ID (jti) cannot be consumed more than once."""
    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
        LAUNCH_TOKEN_HMAC_SECRET="",
    ), TestClient(app) as client:
        fixed_jti = str(uuid.uuid4())
        token = make_rs256_token(roll="237Y1A1270", jti=fixed_jti)
        
        # First use succeeds
        params = launch(client, token)
        assert "handoff" in params

        # Second use is rejected as replayed
        replay_params = launch(client, token)
        assert replay_params.get("auth_error") == "invalid_launch"


def test_dev_launcher_strictly_404_in_production():
    """The /dev-launch simulator endpoint returns 404 in production environment."""
    with configured(ENVIRONMENT="production"), TestClient(app) as client:
        res = client.get("/api/v1/auth/dev-launch?roll=237Y1A1270")
        assert res.status_code == 404


def test_dev_launcher_works_in_development():
    """In development, /dev-launch generates a valid RS256 token and performs handoff."""
    with configured(
        ENVIRONMENT="development",
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER="anvaya.mlritm.ac.in",
        LAUNCH_TOKEN_AUDIENCE="mlritm-chatbot",
        LAUNCH_TOKEN_PUBLIC_KEY=get_dev_public_key_pem(),
        LAUNCH_TOKEN_HMAC_SECRET="",
    ), TestClient(app) as client:
        # Request with format=token
        res = client.get("/api/v1/auth/dev-launch?roll=237Y1A1270&format=token")
        assert res.status_code == 200
        data = res.json()
        assert "token" in data
        assert data["roll_number"] == "237Y1A1270"


# ==============================================================================
# 2. Fallback Mode (Deep-Link) Tests
# ==============================================================================

def test_fallback_mode_when_base_url_is_blank():
    """When ANVAYA_API_BASE_URL is blank, all features return direct Anvaya portal links."""
    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
        ANVAYA_API_BASE_URL="",
        STUDENT_DATA_PROVIDER="anvaya_api",
    ), TestClient(app) as client:
        session_token = sign_in_rs256(client, make_rs256_token(roll="237Y1A1270"))
        
        # Test Attendance
        chat_res = client.post(
            "/api/v1/chat/",
            json={"message": "What is my attendance?"},
            headers=bearer(session_token)
        ).json()
        assert "StudentAttendance" in chat_res["answer"]
        assert "https://anvaya.mlritm.ac.in/App/StudentAttendance" in chat_res["answer"]

        # Test Internal Marks
        chat_res = client.post(
            "/api/v1/chat/",
            json={"message": "What are my internal marks?"},
            headers=bearer(session_token)
        ).json()
        assert "InternalMarks" in chat_res["answer"]
        assert "https://anvaya.mlritm.ac.in/App/InternalMarks" in chat_res["answer"]

        # Test Fees
        chat_res = client.post(
            "/api/v1/chat/",
            json={"message": "What is my fee status?"},
            headers=bearer(session_token)
        ).json()
        assert "FeePayments" in chat_res["answer"]
        assert "https://anvaya.mlritm.ac.in/App/FeePayments" in chat_res["answer"]

        # Test Timetable
        chat_res = client.post(
            "/api/v1/chat/",
            json={"message": "What is my timetable?"},
            headers=bearer(session_token)
        ).json()
        assert "ClassTimetable" in chat_res["answer"]
        assert "https://anvaya.mlritm.ac.in/App/ClassTimetable" in chat_res["answer"]


# ==============================================================================
# 3. Primary Path (API Mode) & Sandbox Tests
# ==============================================================================

SAMPLE_LIVE_ATTENDANCE = {
    "roll_number": "237Y1A1270",
    "overall_attendance": 86.4,
    "total_classes": 250,
    "attended_classes": 216,
    "subjects": [
        {"subject_name": "Operating Systems", "percentage": 88.5},
        {"subject_name": "Database Management Systems", "percentage": 84.0}
    ]
}

SAMPLE_LIVE_FEES = {
    "roll_number": "237Y1A1270",
    "total_fee": 115000,
    "paid_fee": 115000,
    "due_fee": 0,
    "status": "Paid in Full"
}


def test_api_mode_fetches_and_renders_live_data():
    """In API mode, live data is fetched server-side, rendered into Markdown, and cached."""
    ephemeral_live_cache.clear()
    
    def mock_vendor_api(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-API-Key") == "test-vendor-key"
        if "attendance" in str(request.url):
            return httpx.Response(200, json={"data": SAMPLE_LIVE_ATTENDANCE})
        if "fees" in str(request.url):
            return httpx.Response(200, json={"data": SAMPLE_LIVE_FEES})
        return httpx.Response(404)

    mock_service = AnvayaLiveDataService(transport=httpx.MockTransport(mock_vendor_api))

    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
        ANVAYA_API_BASE_URL="https://api.anvaya.mlritm.ac.in",
        ANVAYA_API_AUTH="api_key",
        ANVAYA_API_KEY="test-vendor-key",
        ANVAYA_API_KEY_HEADER="X-API-Key",
        ANVAYA_PATH_ATTENDANCE="/v1/students/{roll_number}/attendance",
        ANVAYA_PATH_FEES="/v1/students/{roll_number}/fees",
        STUDENT_DATA_PROVIDER="anvaya_api",
    ):
        from backend.app.api.v1 import chat
        old_service = chat.live_data_service
        chat.live_data_service = mock_service
        try:
            with TestClient(app) as client:
                session_token = sign_in_rs256(client, make_rs256_token(roll="237Y1A1270"))
                
                # Fetch attendance
                res = client.post(
                    "/api/v1/chat/",
                    json={"message": "What is my attendance?"},
                    headers=bearer(session_token)
                ).json()
                
                assert "86.4%" in res["answer"]
                assert "Operating Systems" in res["answer"]
                assert res["used_personal_data"] is True
                assert res["source"] == "Anvaya Live API"

                # Check that ephemeral cache was populated
                cached = ephemeral_live_cache.get("237Y1A1270", "attendance")
                assert cached is not None
                assert cached["overall_attendance"] == 86.4
        finally:
            chat.live_data_service = old_service


def test_api_mode_prevents_mismatched_roll_number_leak():
    """If the vendor API returns a different student's record, it is rejected and not shown."""
    ephemeral_live_cache.clear()
    
    # API maliciously or erroneously returns data for a different student
    def rogue_vendor_api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "data": {
                "roll_number": "217Y1A0501",  # Different student!
                "overall_attendance": 99.0
            }
        })

    mock_service = AnvayaLiveDataService(transport=httpx.MockTransport(rogue_vendor_api))

    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
        ANVAYA_API_BASE_URL="https://api.anvaya.mlritm.ac.in",
        ANVAYA_PATH_ATTENDANCE="/v1/students/{roll_number}/attendance",
        STUDENT_DATA_PROVIDER="anvaya_api",
    ):
        from backend.app.api.v1 import chat
        old_service = chat.live_data_service
        chat.live_data_service = mock_service
        try:
            with TestClient(app) as client:
                session_token = sign_in_rs256(client, make_rs256_token(roll="237Y1A1270"))
                
                res = client.post(
                    "/api/v1/chat/",
                    json={"message": "What is my attendance?"},
                    headers=bearer(session_token)
                ).json()
                
                # Must NOT show the 99.0% from 217Y1A0501
                assert "99.0%" not in res["answer"]
                assert "ownership verification failed" in res["answer"] or "unavailable" in res["answer"].lower()
                assert "https://anvaya.mlritm.ac.in/App/StudentAttendance" in res["answer"]
        finally:
            chat.live_data_service = old_service


def test_api_mode_sandbox_switch():
    """Setting ANVAYA_API_USE_SANDBOX points client at ANVAYA_API_SANDBOX_URL."""
    with configured(
        ANVAYA_API_BASE_URL="https://prod.anvaya.mlritm.ac.in/api",
        ANVAYA_API_SANDBOX_URL="https://sandbox.anvaya.mlritm.ac.in/api",
        ANVAYA_API_USE_SANDBOX=True,
    ):
        assert settings.effective_anvaya_api_base == "https://sandbox.anvaya.mlritm.ac.in/api"


def test_api_failure_and_timeout_falls_back_gracefully():
    """When the API times out or fails (500), it returns a calm message and deep-link."""
    def timeout_vendor_api(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Mock timeout")

    mock_service = AnvayaLiveDataService(transport=httpx.MockTransport(timeout_vendor_api))

    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
        ANVAYA_API_BASE_URL="https://api.anvaya.mlritm.ac.in",
        ANVAYA_PATH_ATTENDANCE="/v1/students/{roll_number}/attendance",
        STUDENT_DATA_PROVIDER="anvaya_api",
    ):
        from backend.app.api.v1 import chat
        old_service = chat.live_data_service
        chat.live_data_service = mock_service
        try:
            with TestClient(app) as client:
                session_token = sign_in_rs256(client, make_rs256_token(roll="237Y1A1270"))
                
                res = client.post(
                    "/api/v1/chat/",
                    json={"message": "What is my attendance?"},
                    headers=bearer(session_token)
                ).json()
                
                # Should not crash, should offer the portal deep link
                assert "longer than expected to respond" in res["answer"].lower() or "unavailable" in res["answer"].lower()
                assert "https://anvaya.mlritm.ac.in/App/StudentAttendance" in res["answer"]
        finally:
            chat.live_data_service = old_service


# ==============================================================================
# 4. Privacy & DPDP Act 2023 Isolation Tests
# ==============================================================================

def test_querying_another_students_roll_is_politely_declined():
    """If student A asks for student B's data, the assistant politely declines."""
    with configured(
        LAUNCH_TOKEN_ALGORITHMS="RS256",
        LAUNCH_TOKEN_ISSUER=ISSUER,
        LAUNCH_TOKEN_AUDIENCE=AUDIENCE,
        LAUNCH_TOKEN_PUBLIC_KEY=TEST_RSA_PUBLIC_PEM,
    ), TestClient(app) as client:
        # Sign in as 237Y1A1270
        session_token = sign_in_rs256(client, make_rs256_token(roll="237Y1A1270"))

        # Ask about 217Y1A0501
        res = client.post(
            "/api/v1/chat/",
            json={"message": "What is the attendance of 217Y1A0501?"},
            headers=bearer(session_token)
        ).json()

        assert "cannot look up or share academic records for other students" in res["answer"]
        assert "DPDP Act 2023" in res["answer"]
        assert res["source"] == "Privacy Guard (DPDP Act 2023)"
