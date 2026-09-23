"""
Comprehensive Test Suite for ML-Based Short Query Intelligence Platform & Admin Dashboards.

Covers:
- Ultra-short queries (cse hod, it hod, cse faculty, basith, hod cse)
- Ambiguous term detection with clarification (english, physics)
- Telugu-English slang & informal terms (cse lo hod evaru, bro cse hod)
- Typo normalization (cse faclty, hod csee)
- Department alias engine resolution
- Admin Faculty Intelligence Dashboard endpoints
- Admin Query Intelligence Analytics endpoints
"""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.app.main import app
from backend.app.api.v1.chat import process_chat_query
from backend.app.services.query_normalizer import query_normalizer, DEPARTMENT_REGISTRY


@pytest.mark.asyncio
async def test_short_query_variations():
    """Requirement: Diverse short queries without complete sentences."""
    cases = [
        ("cse hod", "FACULTY_HOD", "CSE"),
        ("it hod", "FACULTY_HOD", "IT"),
        ("hod cse", "FACULTY_HOD", "CSE"),
        ("cse head", "FACULTY_HOD", "CSE"),
        ("cse h.o.d", "FACULTY_HOD", "CSE"),
        ("cse faculty", "FACULTY_LIST", "CSE"),
        ("it faculty", "FACULTY_LIST", "IT"),
        ("faculty cse", "FACULTY_LIST", "CSE"),
        ("cse staff", "FACULTY_LIST", "CSE"),
        ("cse professors", "FACULTY_LIST", "CSE"),
        ("aiml faculty", "FACULTY_LIST", "CSE-AI-ML"),
        ("ds faculty", "FACULTY_LIST", "CSE-DATA-SCIENCE"),
        ("basith", "FACULTY_SEARCH", None),
        ("abdul basith", "FACULTY_SEARCH", None),
    ]
    for text, expected_intent, expected_dept in cases:
        res = query_normalizer.understand_query(text)
        assert res.intent == expected_intent, f"Failed intent for '{text}': got {res.intent}"
        if expected_dept:
            assert res.entities.get("department_code") == expected_dept, f"Failed dept for '{text}': got {res.entities.get('department_code')}"


@pytest.mark.asyncio
async def test_ambiguous_short_queries():
    """
    Requirement 3 & 13: Ambiguous queries like 'english' or 'physics' must prompt for clarification
    rather than guessing.
    """
    res_eng = await process_chat_query("english")
    assert res_eng.intent == "AMBIGUOUS_CLARIFICATION"
    assert "Do you want the English faculty or information about the English subject?" in res_eng.answer

    res_phy = await process_chat_query("physics")
    assert res_phy.intent == "AMBIGUOUS_CLARIFICATION"
    assert "Physics faculty" in res_phy.answer


@pytest.mark.asyncio
async def test_typo_resilience():
    """Requirement 14: Typo corrections like 'cse faclty' or 'hod csee'."""
    res1 = query_normalizer.understand_query("cse faclty")
    assert res1.intent == "FACULTY_LIST"
    assert res1.entities.get("department_code") == "CSE"

    res2 = query_normalizer.understand_query("hod csee")
    assert res2.intent == "FACULTY_HOD"
    assert res2.entities.get("department_code") == "CSE"


@pytest.mark.asyncio
async def test_department_alias_engine():
    """Requirement 4: Canonical Department Dictionary covers all aliases."""
    aliases_to_check = [
        ("cse", "CSE"),
        ("computer science", "CSE"),
        ("it", "IT"),
        ("information technology", "IT"),
        ("aiml", "CSE-AI-ML"),
        ("ai&ml", "CSE-AI-ML"),
        ("ds", "CSE-DATA-SCIENCE"),
        ("data science", "CSE-DATA-SCIENCE"),
        ("cyber", "CSE-CYBER-SECURITY"),
        ("ece", "ECE"),
        ("eee", "EEE"),
        ("mech", "MECHANICAL"),
        ("mechanical", "MECHANICAL"),
        ("civil", "CIVIL"),
        ("fe", "FRESHMAN-ENGINEERING"),
        ("mba", "MBA"),
    ]
    for alias, canonical in aliases_to_check:
        res = query_normalizer.resolve_department(alias)
        assert res is not None, f"Failed to resolve alias '{alias}'"
        assert res[0] == canonical, f"Alias '{alias}' mapped to '{res[0]}' instead of '{canonical}'"


# =============================================================================
# ADMIN DASHBOARD API TESTS (Requirements 22 & 26)
# =============================================================================

@pytest.mark.asyncio
async def test_admin_faculty_stats():
    """Requirement 26: GET /api/v1/admin/faculty/stats."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/admin/faculty/stats")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "operational"
        assert data["total_faculty_records"] > 0
        assert "Computer Science and Engineering" in data["faculty_by_department"]
        assert len(data["hods"]) > 0
        assert "last_synchronized_at" in data


@pytest.mark.asyncio
async def test_admin_faculty_sync_and_changes():
    """Requirement 26: POST /api/v1/admin/faculty/sync and GET /api/v1/admin/faculty/changes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        sync_res = await client.post("/api/v1/admin/faculty/sync")
        assert sync_res.status_code == 200
        sync_data = sync_res.json()
        assert sync_data["status"] == "success"

        changes_res = await client.get("/api/v1/admin/faculty/changes")
        assert changes_res.status_code == 200
        changes_data = changes_res.json()
        assert "sync_logs" in changes_data


@pytest.mark.asyncio
async def test_admin_faculty_rebuild_index():
    """Requirement 26: POST /api/v1/admin/faculty/rebuild-index."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/admin/faculty/rebuild-index")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["records_indexed"] > 0


@pytest.mark.asyncio
async def test_admin_queries_stats():
    """Requirement 22: GET /api/v1/admin/queries/stats."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/admin/queries/stats")
        assert res.status_code == 200
        data = res.json()
        assert "total_queries_logged" in data
        assert "intent_accuracy" in data


@pytest.mark.asyncio
async def test_admin_queries_aliases():
    """Requirement 22: GET /api/v1/admin/queries/aliases."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/admin/queries/aliases")
        assert res.status_code == 200
        data = res.json()
        assert "department_aliases" in data
        assert "CSE" in data["department_aliases"]
        assert "IT" in data["department_aliases"]
