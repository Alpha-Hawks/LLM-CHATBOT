"""
Tests for Anvaya SSO Login Flow, Background Data Synchronization, and Student Personalization.

Verifies:
1. Anvaya SSO redirect and authorization code generation.
2. Callback handler validating single-use authorization code and handing off to PWA.
3. Session token exchange and student identity extraction (anvaya_user_id, student_id, roll_number).
4. Multi-student cohort resolution & strict isolation:
   - Student A (IT Year 2, Sem 4): 237Y1A1270
   - Student B (CSE Year 4, Sem 7): 217Y1A0501
   - Student C (ECE Year 3, Sem 5): 227Y1A0415
5. Background data synchronization and database persistence of student profile.
6. Context engine filtering and prompt injection: student data isolation, current semester resolution.
7. Logout and session termination.
"""

from fastapi.testclient import TestClient

from backend.app.main import app
from tests.auth_test_utils import bearer, configured


def _authorize_anvaya_student(client: TestClient, roll: str, name: str, dept: str, sem: int = 4):
    """Simulates Anvaya SSO authorization and returns the chatbot session token."""
    auth_res = client.post(
        "/api/v1/auth/anvaya/authorize",
        json={
            "roll_number": roll,
            "name": name,
            "department": dept,
            "current_semester": sem,
        },
        headers={"Accept": "application/json"},
    )
    assert auth_res.status_code == 200, auth_res.text
    auth_data = auth_res.json()
    assert auth_data["success"] is True
    redirect_url = auth_data["redirect_url"]

    # Follow callback
    cb_res = client.get(redirect_url, follow_redirects=False)
    assert cb_res.status_code == 303
    location = cb_res.headers["location"]
    assert "/app/#handoff=" in location

    handoff_code = location.split("#handoff=")[1]

    # Exchange handoff code for session token
    ex_res = client.post("/api/v1/auth/session/exchange", json={"handoff_code": handoff_code})
    assert ex_res.status_code == 200, ex_res.text
    session_token = ex_res.json()["session_token"]

    # Grant DPDP consent
    consent_res = client.post(
        "/api/v1/auth/consent",
        json={"dpdp_consent_granted": True},
        headers=bearer(session_token),
    )
    assert consent_res.status_code == 200

    return session_token


def test_anvaya_login_endpoint():
    """Verifies that the Anvaya login configuration endpoint returns valid URLs."""
    with configured(), TestClient(app) as client:
        res = client.get("/api/v1/auth/anvaya/login", headers={"Accept": "application/json"})
        assert res.status_code == 200
        data = res.json()
        assert data["anvaya_sso_enabled"] is True
        assert "/anvaya/login.html" in data["anvaya_login_url"]
        assert "students" in data
        assert len(data["students"]) >= 3


def test_anvaya_sso_full_flow():
    """Verifies the complete Anvaya SSO login, exchange, profile sync, and identity retrieval."""
    with configured(), TestClient(app) as client:
        token = _authorize_anvaya_student(
            client,
            roll="237Y1A1270",
            name="Gunda Dinesh",
            dept="Information Technology",
            sem=4,
        )

        # 1. Check /auth/me
        me_res = client.get("/api/v1/auth/me", headers=bearer(token))
        assert me_res.status_code == 200
        student = me_res.json()["student"]
        assert student["roll_number"] == "237Y1A1270"
        assert student["name"] == "Gunda Dinesh"
        assert student["anvaya_user_id"] == "anvaya-237y1a1270"
        assert student["student_id"] == "237Y1A1270"
        assert student["consent_granted"] is True

        # 2. Check /student/profile
        prof_res = client.get("/api/v1/student/profile", headers=bearer(token))
        assert prof_res.status_code == 200
        prof = prof_res.json()
        assert prof["connected"] is True
        assert prof["current_semester"] == 4
        assert prof["identity"]["roll_number"] == "237Y1A1270"
        assert prof["identity"]["department"] == "Information Technology"
        assert prof["counts"]["subjects"] == 7
        assert prof["attendance_summary"]["overall_percentage"] > 0


def test_anvaya_multi_student_cohort_isolation():
    """
    Verifies that Student A (IT, Sem 4), Student B (CSE, Sem 7), and Student C (ECE, Sem 5)
    each receive strictly their own cohort data, faculty, and timetable.
    """
    with configured(), TestClient(app) as client:
        token_it = _authorize_anvaya_student(
            client, roll="237Y1A1270", name="Gunda Dinesh", dept="Information Technology", sem=4
        )
        token_cse = _authorize_anvaya_student(
            client, roll="217Y1A0501", name="M. Rohith", dept="Computer Science & Engineering", sem=7
        )
        token_ece = _authorize_anvaya_student(
            client, roll="227Y1A0415", name="A. Teja", dept="Electronics & Communication Engineering", sem=5
        )

        # --- Student A (IT Year 2, Sem 4) ---
        chat_it_sub = client.post(
            "/api/v1/chat/",
            json={"message": "Which subjects am I studying this semester?"},
            headers=bearer(token_it),
        ).json()
        assert "IT401PC" in chat_it_sub["answer"]
        assert "CS701PC" not in chat_it_sub["answer"]
        assert "EC501PC" not in chat_it_sub["answer"]

        chat_it_fac = client.post(
            "/api/v1/chat/",
            json={"message": "Who is my DBMS faculty?"},
            headers=bearer(token_it),
        ).json()
        assert "Dr. S. Sharma" in chat_it_fac["answer"]

        chat_it_tt = client.post(
            "/api/v1/chat/",
            json={"message": "What is my timetable?"},
            headers=bearer(token_it),
        ).json()
        assert "Information Technology" in chat_it_tt["answer"]

        # --- Student B (CSE Year 4, Sem 7) ---
        chat_cse_sub = client.post(
            "/api/v1/chat/",
            json={"message": "Which subjects am I studying this semester?"},
            headers=bearer(token_cse),
        ).json()
        assert "CS701PC" in chat_cse_sub["answer"]
        assert "IT401PC" not in chat_cse_sub["answer"]
        assert "EC501PC" not in chat_cse_sub["answer"]

        chat_cse_fac = client.post(
            "/api/v1/chat/",
            json={"message": "Who is my Cloud Computing faculty?"},
            headers=bearer(token_cse),
        ).json()
        assert "Dr. P. Reddy" in chat_cse_fac["answer"]

        # --- Student C (ECE Year 3, Sem 5) ---
        chat_ece_sub = client.post(
            "/api/v1/chat/",
            json={"message": "Which subjects am I studying this semester?"},
            headers=bearer(token_ece),
        ).json()
        assert "EC501PC" in chat_ece_sub["answer"]
        assert "IT401PC" not in chat_ece_sub["answer"]
        assert "CS701PC" not in chat_ece_sub["answer"]


def test_anvaya_force_sync():
    """Verifies that POST /api/v1/student/sync refreshes the profile snapshot."""
    with configured(), TestClient(app) as client:
        token = _authorize_anvaya_student(
            client, roll="237Y1A1270", name="Gunda Dinesh", dept="Information Technology", sem=4
        )
        sync_res = client.post("/api/v1/student/sync", headers=bearer(token))
        assert sync_res.status_code == 200
        data = sync_res.json()
        assert data["connected"] is True
        assert data["status"] == "fresh"
        assert data["identity"]["roll_number"] == "237Y1A1270"


def test_anvaya_logout_and_session_invalidation():
    """Verifies that logging out revokes the session and clears access."""
    with configured(), TestClient(app) as client:
        token = _authorize_anvaya_student(
            client, roll="237Y1A1270", name="Gunda Dinesh", dept="Information Technology", sem=4
        )

        # Confirm access works
        me_before = client.get("/api/v1/auth/me", headers=bearer(token))
        assert me_before.status_code == 200

        # Logout
        logout_res = client.post("/api/v1/auth/logout", headers=bearer(token))
        assert logout_res.status_code == 200

        # Confirm access is revoked
        me_after = client.get("/api/v1/auth/me", headers=bearer(token))
        assert me_after.status_code == 401

        # Confirm student records are inaccessible
        prof_after = client.get("/api/v1/student/profile", headers=bearer(token))
        assert prof_after.status_code == 401


def test_anvaya_single_use_auth_code():
    """Verifies that Anvaya authorization codes cannot be reused (anti-replay)."""
    with configured(), TestClient(app) as client:
        auth_res = client.post(
            "/api/v1/auth/anvaya/authorize",
            json={
                "roll_number": "237Y1A1270",
                "name": "Gunda Dinesh",
                "department": "Information Technology",
                "current_semester": 4,
            },
            headers={"Accept": "application/json"},
        )
        assert auth_res.status_code == 200
        redirect_url = auth_res.json()["redirect_url"]

        # Follow callback first time
        cb_res_1 = client.get(redirect_url, follow_redirects=False)
        assert cb_res_1.status_code == 303
        assert "/app/#handoff=" in cb_res_1.headers["location"]

        # Replay callback with the same code
        cb_res_2 = client.get(redirect_url, follow_redirects=False)
        assert cb_res_2.status_code == 303
        assert "invalid_or_expired_code" in cb_res_2.headers["location"]


def test_exact_roll_matching_and_live_data_for_237Y1A1201():
    """
    Verifies that authenticating with roll number 237Y1A1201 strictly retrieves
    that student's authorized records, answers lowest attendance in OS (74%),
    reports DBMS attendance as 82%, and shows Data Status.
    """
    with configured(), TestClient(app) as client:
        token = _authorize_anvaya_student(
            client,
            roll="237Y1A1201",
            name="Student 237Y1A1201",
            dept="Information Technology",
            sem=4,
        )

        # 1. Profile must show exact roll number and Data Status
        prof_res = client.get("/api/v1/student/profile", headers=bearer(token))
        assert prof_res.status_code == 200
        prof = prof_res.json()
        assert prof["identity"]["roll_number"] == "237Y1A1201"
        assert prof["identity"]["department"] == "Information Technology"
        assert prof["identity"]["year"] == "2nd Year"
        assert prof["current_semester"] == 4
        assert prof["data_status"] in ("Live", "Recently Synced")
        assert "last_synced_formatted" in prof

        # 2. Ask "What is my DBMS attendance?" -> 82%
        dbms_chat = client.post(
            "/api/v1/chat/",
            json={"message": "What is my DBMS attendance?"},
            headers=bearer(token),
        ).json()
        assert "82%" in dbms_chat["answer"]

        # 3. Ask "Which subject has my lowest attendance?" -> OS at 74%
        lowest_chat = client.post(
            "/api/v1/chat/",
            json={"message": "Which subject has my lowest attendance?"},
            headers=bearer(token),
        ).json()
        assert "Operating Systems" in lowest_chat["answer"]
        assert "74%" in lowest_chat["answer"]


def test_tampered_url_params_ignored():
    """
    Verifies that passing ?rollno=237Y1A1202 or any query param to student endpoints
    is strictly ignored and only the authenticated student's data is returned.
    """
    with configured(), TestClient(app) as client:
        token = _authorize_anvaya_student(
            client,
            roll="237Y1A1201",
            name="Student 237Y1A1201",
            dept="Information Technology",
            sem=4,
        )

        # Attempt to access another student via query parameter
        res = client.get("/api/v1/student/profile?rollno=237Y1A1202", headers=bearer(token))
        assert res.status_code == 200
        body = res.json()
        assert body["identity"]["roll_number"] == "237Y1A1201"
        assert "237Y1A1202" not in str(body)

