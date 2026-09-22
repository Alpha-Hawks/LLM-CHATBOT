"""
Shared helpers for identity / authorization tests.
Configures a signed-launch integration in-process and mints launch tokens the way Anvaya would.
"""

import contextlib
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import jwt

from backend.app.core.config import settings

TEST_ISSUER = "https://anvaya.example.test"
TEST_AUDIENCE = "mlritm-ai-assistant"
TEST_HMAC_SECRET = "test-launch-secret-0123456789abcdef-XYZ"

BASE_SETTINGS = dict(
    ENVIRONMENT="development",
    IDENTITY_PROVIDER="signed_launch",
    LAUNCH_TOKEN_ISSUER=TEST_ISSUER,
    LAUNCH_TOKEN_AUDIENCE=TEST_AUDIENCE,
    LAUNCH_TOKEN_ALGORITHMS="HS256",
    LAUNCH_TOKEN_HMAC_SECRET=TEST_HMAC_SECRET,
    LAUNCH_TOKEN_PUBLIC_KEY="",
    LAUNCH_TOKEN_PUBLIC_KEY_FILE="",
    LAUNCH_TOKEN_JWKS_URL="",
    LAUNCH_TOKEN_MAX_AGE_SECONDS=300,
    IDENTITY_STUDENT_KEY_CLAIM="sub",
    IDENTITY_ROLL_NUMBER_CLAIM="roll_no",
    IDENTITY_NAME_CLAIM="name",
    IDENTITY_ROLE_CLAIM="",
    IDENTITY_STUDENT_ROLE_VALUES="",
    OIDC_ISSUER="",
    OIDC_CLIENT_ID="",
    OIDC_CLIENT_SECRET="",
    OIDC_REDIRECT_URI="",
    OIDC_SCOPES="openid profile",
    STUDENT_DATA_PROVIDER="sample",
    # Re-read the provider on every request so each test exercises the real synchronization
    # path instead of a snapshot an earlier test happened to leave behind.
    PROFILE_SYNC_TTL_SECONDS=0,
    PROFILE_MAX_STALE_SECONDS=86400,
    ANVAYA_API_BASE_URL="",
    ANVAYA_API_STUDENT_PATH="",
)


@contextlib.contextmanager
def configured(**overrides):
    """Temporarily applies integration settings, restoring the originals afterwards."""
    values = {**BASE_SETTINGS, **overrides}
    saved = {k: getattr(settings, k) for k in values}
    for k, v in values.items():
        setattr(settings, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)


def make_launch_token(sub="student-a", roll="ROLL-A", key=TEST_HMAC_SECRET, algorithm="HS256", headers=None, **claims):
    """Mints a launch token. Pass claim=None to omit a claim."""
    now = int(time.time())
    payload = {
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "sub": sub,
        "roll_no": roll,
        "name": f"Student {sub}",
        "iat": now,
        "exp": now + 120,
        "jti": str(uuid.uuid4()),
    }
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm=algorithm, headers=headers)


def fragment_params(location: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(location).fragment).items()}


def launch(client, token: str) -> dict:
    """Performs the Anvaya launch redirect and returns the #fragment parameters."""
    res = client.get("/api/v1/auth/launch", params={"launch_token": token}, follow_redirects=False)
    assert res.status_code == 303, res.text
    assert res.headers["location"].startswith("/app/#")
    return fragment_params(res.headers["location"])


def sign_in(client, sub="student-a", roll="ROLL-A", consent=True, name=None) -> str:
    """Anvaya launch -> handoff exchange (-> DPDP consent). Returns the chatbot session token."""
    claims = {"name": name} if name else {}
    params = launch(client, make_launch_token(sub=sub, roll=roll, **claims))
    assert "handoff" in params, params
    res = client.post("/api/v1/auth/session/exchange", json={"handoff_code": params["handoff"]})
    assert res.status_code == 200, res.text
    token = res.json()["session_token"]
    if consent:
        res = client.post("/api/v1/auth/consent", json={"dpdp_consent_granted": True}, headers=bearer(token))
        assert res.status_code == 200, res.text
    return token


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@contextlib.contextmanager
def rogue_profile_provider(provider):
    """
    Substitutes the student data provider that the synchronization service resolves.

    Patching this seam is how the isolation tests prove that a misbehaving or compromised data
    source cannot deliver one student's records to another.
    """
    from backend.app.services import sync_service

    original = sync_service.get_student_data_provider
    sync_service.get_student_data_provider = lambda: provider
    try:
        yield
    finally:
        sync_service.get_student_data_provider = original


def student_context(sub="student-a", roll="ROLL-A", consent=True, session_id=1, display_name="Test Student"):
    """
    A StudentContext exactly as the session layer would rebuild it.

    `display_name` is deliberately unrelated to `sub`, so a test asserting that the internal
    student key never leaves the backend cannot pass or fail by accident on the name.
    """
    from backend.app.services.identity.base import StudentContext

    return StudentContext(
        session_id=session_id,
        subject=sub,
        student_key=sub,
        roll_number=roll,
        display_name=display_name,
        consent_granted=consent,
    )


async def sample_profile(student):
    """The unmodified development snapshot, for tests that then tamper with one field."""
    from backend.app.services.student_data.providers import SampleStudentDataProvider

    return await SampleStudentDataProvider().get_profile(student)
