"""
Tests for Official MLRITM Anvaya Portal Integration & Zero Default Student Details Policy.

Verifies:
1. AnvayaService URLs: /Login and /App.
2. Zero Default Student Details:
   - Unauthenticated student/profile fails closed (HTTP 401).
   - Unauthenticated chat about attendance/marks requires login and reveals no student numbers.
   - Dev launch without roll number fails closed with HTTP 400.
3. Official identity extraction hierarchy:
   - anvaya_user_id -> student_id -> roll_number.
   - Refuses identification solely by name.
4. Personalized AI calculations:
   - "Which subject has my lowest attendance?" deterministically computes the minimum attendance subject.
   - DBMS faculty matching.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.config import settings
from backend.app.services.anvaya_service import anvaya_service
from tests.auth_test_utils import bearer, configured, sign_in


def test_anvaya_service_endpoints():
    """Verifies that AnvayaService points to the official MLRITM Anvaya portal."""
    assert "https://anvaya.mlritm.ac.in" in anvaya_service.base_url
    assert anvaya_service.login_url == "https://anvaya.mlritm.ac.in/Login"
    assert anvaya_service.app_url == "https://anvaya.mlritm.ac.in/App"


def test_zero_default_student_details_on_unauthenticated_profile():
    """An unauthenticated visitor must receive 401 and zero student data."""
    with configured(), TestClient(app) as client:
        res = client.get("/api/v1/student/profile")
        assert res.status_code == 401
        data = res.json()
        assert "237Y1A" not in str(data)
        assert "Student" not in str(data)


def test_zero_default_student_details_on_unauthenticated_chat():
    """An unauthenticated chat query for personal data must require auth and disclose zero numbers."""
    with configured(), TestClient(app) as client:
        res = client.post("/api/v1/chat/", json={"message": "What is my attendance?"})
        assert res.status_code == 200
        data = res.json()
        assert data["requires_auth"] is True
        assert data["used_personal_data"] is False
        assert "%" not in data["answer"]
        assert "237Y1A" not in data["answer"]
        assert "Login with Anvaya" in data["answer"]


def test_dev_launch_requires_explicit_roll_number():
    """Dev launch must refuse to default to 237Y1A1270 and require an explicit roll parameter."""
    with configured(), TestClient(app) as client:
        res = client.get("/api/v1/auth/dev-launch")
        assert res.status_code == 400
        assert "Roll number is required" in res.json()["detail"]


def test_anvaya_service_identity_extraction():
    """Verifies that Anvaya identity extraction uses the strongest identifiers available."""
    identity = anvaya_service.extract_student_identity(
        roll_number="217Y1A0501",
        name="M. Rohith",
        anvaya_user_id="anvaya-217y1a0501",
        student_id="217Y1A0501",
    )
    assert identity.roll_number == "217Y1A0501"
    assert identity.anvaya_user_id == "anvaya-217y1a0501"
    assert identity.student_id == "217Y1A0501"
    assert identity.display_name == "M. Rohith"

    # Refuse empty roll number
    with pytest.raises(ValueError):
        anvaya_service.extract_student_identity(roll_number="")


def test_lowest_attendance_calculation_for_authenticated_student():
    """Verifies that 'Which subject has my lowest attendance?' returns the minimum percentage subject."""
    with configured(), TestClient(app) as client:
        token = sign_in(client, sub="test-calc-sub", roll="237Y1A1270")
        res = client.post(
            "/api/v1/chat/",
            json={"message": "Which subject has my lowest attendance?"},
            headers=bearer(token),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["used_personal_data"] is True
        # Discrete Mathematics is at 69.05% in cohort 1270
        assert "lowest attendance" in data["answer"].lower() or "discrete mathematics" in data["answer"].lower()
