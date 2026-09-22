"""
Tests for the Anvaya personalization layer.

Covers the four properties the layer exists to guarantee:

    1. answers are about the signed-in student, in their current semester
    2. only the fields a question needs are selected - never the whole record
    3. one student can never reach another student's data by any route
    4. when Anvaya cannot be read, the assistant says so instead of inventing anything
"""

import asyncio

from fastapi.testclient import TestClient

from backend.app.llm.intent_router import IntentEnum, IntentRouter
from backend.app.services import context_engine
from backend.app.services.student_data.base import StudentDataUnavailable
from backend.app.services.student_data.schemas import StudentAcademicProfile
from backend.app.main import app
from tests.auth_test_utils import bearer, configured, rogue_profile_provider, sample_profile, sign_in, student_context


def _chat(client, message, token=None):
    res = client.post("/api/v1/chat/", json={"message": message}, headers=bearer(token) if token else {})
    assert res.status_code == 200, res.text
    return res.json()


# =============================================================================================
# 1. Personalized answers
# =============================================================================================

def test_each_quick_action_answers_from_the_students_own_record():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-quick", roll="237Y1A1270")
        expected = {
            "What is my attendance?": "Attendance Report",
            "Which subjects am I studying this semester?": "Your Semester 4 Subjects",
            "What are my internal marks?": "Internal Marks",
            "Who are my faculty members?": "Your Faculty",
            "What is my class timetable?": "Your Class Timetable",
            "What is my examination schedule?": "Your Examination Schedule",
            "What are my latest results?": "Semester Examination Results",
            "Show my academic profile.": "Your Academic Profile",
        }
        for question, heading in expected.items():
            data = _chat(c, question, token)
            assert data["used_personal_data"] is True, question
            assert heading in data["answer"], f"{question} -> {data['answer'][:200]}"


def test_faculty_question_returns_only_the_named_subject():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-faculty", roll="237Y1A1270")
        answer = _chat(c, "Who is my DBMS faculty?", token)["answer"]

        assert "Database Management Systems" in answer
        assert "Dr. S. Sharma" in answer
        # The other six subjects of the semester are not part of this question
        assert "Discrete Mathematics" not in answer
        assert "Operating Systems" not in answer


def test_low_attendance_question_lists_only_the_subjects_below_the_threshold():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-low", roll="237Y1A1270")
        answer = _chat(c, "Which subjects have low attendance?", token)["answer"]

        # Below 75% in the development cohort
        assert "Computer Organization & Architecture" in answer
        assert "Discrete Mathematics" in answer
        # At or above 75%, so not an answer to "which are low"
        assert "Java Programming Lab" not in answer


def test_answers_are_scoped_to_the_students_current_semester():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-sem", roll="237Y1A1270")
        data = _chat(c, "Which subjects am I studying this semester?", token)

        assert data["current_semester"] == 4
        assert "Semester 4" in data["answer"]
        # Semester 4 subject codes, not another semester's
        assert "IT401PC" in data["answer"]
        assert "CS501PC" not in data["answer"]


def test_two_students_get_their_own_cohorts():
    with configured(), TestClient(app) as c:
        it_token = sign_in(c, sub="p-it", roll="237Y1A1270")
        cs_token = sign_in(c, sub="p-cs", roll="237Y1A0599")

        it_answer = _chat(c, "Which subjects am I studying this semester?", it_token)["answer"]
        cs_answer = _chat(c, "Which subjects am I studying this semester?", cs_token)["answer"]

        assert "IT401PC" in it_answer and "CS501PC" not in it_answer
        assert "CS501PC" in cs_answer and "IT401PC" not in cs_answer


# =============================================================================================
# 2. Minimal context
# =============================================================================================

def _bundle_for(question, intent, sub="p-ctx"):
    async def run():
        from backend.app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await context_engine.build(db, student_context(sub, "237Y1A1270"), intent, question)

    return asyncio.run(run())


def test_bundle_holds_only_the_category_the_question_asked_for():
    with configured():
        result = _bundle_for("What is my attendance?", IntentEnum.ATTENDANCE)
        assert result.available
        assert set(result.bundle.sections) == {"attendance"}

        result = _bundle_for("Who is my DBMS faculty?", IntentEnum.FACULTY)
        assert set(result.bundle.sections) == {"faculty"}


def test_bundle_never_carries_the_student_key_or_session_details():
    """The key that identifies the student to Anvaya must not travel with the context."""
    with configured():
        result = _bundle_for("Show my academic profile.", IntentEnum.PROFILE, sub="internal-key-9f2a")
        rendered = result.bundle.model_dump_json() + result.bundle.to_prompt_block()

        assert "Test Student" in rendered                  # the display name is meant to be there
        for secret in ("student_key", "internal-key-9f2a", "session_id", "token_hash", "student_profiles"):
            assert secret not in rendered, f"{secret} leaked into the context bundle"


def test_answers_do_not_expose_internal_identifiers():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-leak", roll="237Y1A1270", name="Priya K")
        answer = _chat(c, "What is my attendance?", token)["answer"]

        for secret in ("student_key", "p-leak", "student_profiles", "Bearer", "token_hash"):
            assert secret not in answer


def test_general_questions_need_no_personal_data():
    with configured():
        assert context_engine.requires_personal_data(IntentEnum.FAQ_RAG, "What is the B.Tech tuition fee?") is False
        assert context_engine.requires_personal_data(IntentEnum.HOLIDAYS, "Is tomorrow a holiday?") is False
        assert context_engine.requires_personal_data(IntentEnum.ATTENDANCE, "What is my attendance?") is True


# =============================================================================================
# 3. Isolation
# =============================================================================================

def test_record_endpoints_refuse_without_a_session_or_consent():
    with configured(), TestClient(app) as c:
        assert c.get("/api/v1/student/profile").status_code == 401
        assert c.get("/api/v1/student/record/attendance").status_code == 401
        assert c.post("/api/v1/student/sync").status_code == 401

        no_consent = sign_in(c, sub="p-noconsent", roll="ROLL-N", consent=False)
        assert c.get("/api/v1/student/record/attendance", headers=bearer(no_consent)).status_code == 403
        assert c.get("/api/v1/student/profile", headers=bearer(no_consent)).status_code == 403


def test_record_endpoint_serves_only_the_session_holders_data():
    with configured(), TestClient(app) as c:
        token_a = sign_in(c, sub="p-iso-a", roll="ROLL-ISO-A")
        sign_in(c, sub="p-iso-b", roll="ROLL-ISO-B")

        body = c.get("/api/v1/student/record/attendance", headers=bearer(token_a)).json()
        assert "ROLL-ISO-A" in body["formatted_markdown"]
        assert "ROLL-ISO-B" not in body["formatted_markdown"]

        profile = c.get("/api/v1/student/profile", headers=bearer(token_a)).json()
        assert profile["identity"]["roll_number"] == "ROLL-ISO-A"


def test_profile_endpoint_does_not_return_internal_fields():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-fields", roll="ROLL-F", name="Arjun M")
        body = c.get("/api/v1/student/profile", headers=bearer(token)).json()

        assert body["identity"]["name"] == "Arjun M"
        assert "student_key" not in str(body)
        assert "p-fields" not in str(body)
        assert set(body["identity"]) == {
            "name", "roll_number", "department", "course", "year", "section", "academic_year",
        }


def test_unknown_record_category_is_not_reachable():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-unknown", roll="ROLL-U")
        # The category is a closed enumeration, so nothing outside it reaches a query
        assert c.get("/api/v1/student/record/everything", headers=bearer(token)).status_code == 422
        assert c.get("/api/v1/student/record/student_profiles", headers=bearer(token)).status_code == 422


# =============================================================================================
# 4. Honest fallback
# =============================================================================================

class _UnavailableProvider:
    name = "sample"

    async def get_profile(self, student):
        raise StudentDataUnavailable("Anvaya is not responding right now, so I could not load your records.")


def test_outage_with_nothing_stored_invents_nothing():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-outage-new", roll="ROLL-ON")
        with rogue_profile_provider(_UnavailableProvider()):
            data = _chat(c, "What is my attendance?", token)

        assert data["used_personal_data"] is False
        assert "not responding" in data["answer"]
        assert "%" not in data["answer"].split("Anvaya")[0]     # no invented figure before the explanation
        assert "Attendance Report" not in data["answer"]


def test_outage_after_a_successful_sync_serves_the_last_snapshot_marked_stale():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-outage-known", roll="237Y1A1270")
        assert "72.08%" in _chat(c, "What is my attendance?", token)["answer"]

        with rogue_profile_provider(_UnavailableProvider()):
            data = _chat(c, "What is my attendance?", token)

        assert "72.08%" in data["answer"]
        assert "could not be reached" in data["answer"]         # the staleness is stated, not hidden


def test_records_from_a_different_source_are_not_reused():
    """Switching the configured interface must not let an old snapshot answer as the new one."""

    class OtherSourceProvider:
        name = "anvaya_api"

        async def get_profile(self, student):
            real = await sample_profile(student)
            return StudentAcademicProfile(**{**real.model_dump(), "department": "Official Department", "source": "Anvaya ERP"})

    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-switch", roll="237Y1A1270")
        first = c.get("/api/v1/student/profile", headers=bearer(token)).json()
        assert first["identity"]["department"] == "Information Technology"

        with rogue_profile_provider(OtherSourceProvider()):
            second = c.get("/api/v1/student/profile", headers=bearer(token)).json()

        assert second["identity"]["department"] == "Official Department"
        assert second["source"] == "Anvaya ERP"


def test_semester_change_is_picked_up_on_the_next_sync():
    class NextSemesterProvider:
        name = "sample"

        async def get_profile(self, student):
            real = await sample_profile(student)
            return StudentAcademicProfile(**{**real.model_dump(), "current_semester": 5})

    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-semchange", roll="237Y1A1270")
        assert c.get("/api/v1/student/profile", headers=bearer(token)).json()["current_semester"] == 4

        with rogue_profile_provider(NextSemesterProvider()):
            body = c.post("/api/v1/student/sync", headers=bearer(token)).json()

        assert body["current_semester"] == 5
        assert body["semester_changed"] is True


def test_sync_endpoint_refreshes_the_snapshot():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-sync", roll="237Y1A1270")
        first = c.get("/api/v1/student/profile", headers=bearer(token)).json()
        second = c.post("/api/v1/student/sync", headers=bearer(token)).json()

        assert second["status"] == "fresh"
        assert second["last_synced_at"] >= first["last_synced_at"]
        assert second["connected"] is True


def test_erasing_the_snapshot_removes_the_stored_copy():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-erase", roll="237Y1A1270")
        c.get("/api/v1/student/profile", headers=bearer(token))

        assert c.delete("/api/v1/student/data", headers=bearer(token)).status_code == 200

        async def stored_rows():
            from sqlalchemy import select
            from backend.app.db.models import StudentProfile
            from backend.app.db.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                rows = await db.execute(select(StudentProfile).where(StudentProfile.student_key == "p-erase"))
                return rows.scalars().all()

        assert asyncio.run(stored_rows()) == []


def test_records_stay_unavailable_when_no_interface_is_configured():
    with configured(STUDENT_DATA_PROVIDER="none"), TestClient(app) as c:
        token = sign_in(c, sub="p-noprovider", roll="ROLL-NP")
        for question in ("What are my internal marks?", "Who are my faculty members?", "Show my academic profile."):
            data = _chat(c, question, token)
            assert "not connected yet" in data["answer"], question
            assert data["used_personal_data"] is False


# =============================================================================================
# Intent routing for the new categories
# =============================================================================================

def test_new_personal_questions_route_to_their_category():
    router = IntentRouter()
    cases = [
        ("Which subjects am I studying this semester?", IntentEnum.SUBJECTS),
        ("What are my subjects?", IntentEnum.SUBJECTS),
        ("Who is my DBMS faculty?", IntentEnum.FACULTY),
        ("Who teaches Operating Systems?", IntentEnum.FACULTY),
        ("What are my internal marks?", IntentEnum.MARKS),
        ("Show my mid 1 marks", IntentEnum.MARKS),
        ("When is my next exam?", IntentEnum.EXAMS),
        ("Show my exam schedule", IntentEnum.EXAMS),
        ("What assignments are pending?", IntentEnum.ASSIGNMENTS),
        ("Show my academic profile", IntentEnum.PROFILE),
        ("Which semester am I in?", IntentEnum.PROFILE),
        ("Which subjects have low attendance?", IntentEnum.ATTENDANCE),
        ("What is my attendance?", IntentEnum.ATTENDANCE),
    ]
    for question, expected in cases:
        assert router.route(question).intent == expected, f"{question} -> {router.route(question).intent}"


def test_existing_categories_still_route_the_same_way():
    """The new intents must not steal questions the assistant already answered correctly."""
    router = IntentRouter()
    unchanged = [
        ("What are my grades in last exams?", IntentEnum.RESULTS),
        ("Do I have any active backlogs?", IntentEnum.RESULTS),
        ("Did I pass in Operating Systems?", IntentEnum.RESULTS),
        ("Who is taking the next lecture?", IntentEnum.TIMETABLE),
        ("Which room is my next class in?", IntentEnum.TIMETABLE),
        ("What are the semester end exam dates?", IntentEnum.HOLIDAYS),
        ("What is the B.Tech CSE tuition fee?", IntentEnum.FAQ_RAG),
        ("Can I get condonation if attendance is 62%?", IntentEnum.FAQ_RAG),
        ("Someone is ragging a junior near the canteen", IntentEnum.ESCALATE),
        ("Hello!", IntentEnum.SMALL_TALK),
    ]
    for question, expected in unchanged:
        assert router.route(question).intent == expected, f"{question} -> {router.route(question).intent}"


# =============================================================================================
# General + personal knowledge
# =============================================================================================

def test_advice_question_shows_the_record_and_the_policy_separately():
    with configured(), TestClient(app) as c:
        token = sign_in(c, sub="p-advice", roll="237Y1A1270")
        data = _chat(c, "What should I do because my attendance is below the required percentage?", token)

        assert data["used_personal_data"] is True
        assert data["used_knowledge_base"] is True
        assert "From your Anvaya record" in data["answer"]
        assert "Official MLRITM information" in data["answer"]
        assert "72.08%" in data["answer"]


def test_general_questions_still_work_without_signing_in():
    with configured(), TestClient(app) as c:
        data = _chat(c, "What is the B.Tech CSE tuition fee?")
        assert data["intent"] == "FAQ_RAG"
        assert data["requires_auth"] is False
        assert "Official Regulations" in data["answer"] or "1,05,000" in data["answer"]


def test_greeting_is_personalized_once_signed_in():
    with configured(), TestClient(app) as c:
        assert "Login with Anvaya" in _chat(c, "Hello!")["answer"]

        token = sign_in(c, sub="p-greet", roll="ROLL-G")
        assert "Student p-greet" in _chat(c, "Hello!", token)["answer"]
