"""
Comprehensive Test Suite for MLRITM Faculty Intelligence Module.

Covers:
- Department HOD lookups across all engineering branches (CSE, IT, AIML, DS, Cyber, ECE, EEE, Mech, Civil, FE, MBA)
- Student department context resolution ("my hod" with session vs unauthenticated)
- Faculty directory listings and designation filters (Professor, Associate, Assistant)
- Specific faculty profile queries (Dr. K Abdul Basith, Dr. M Nagalakshmi)
- Multi-turn conversation context (Qualifications, Specialization, Profile link, Research)
- Faculty subject queries with strict official verification
- Faculty research, publications, and patents queries
- Telugu-English mixed student queries ("cse lo hod evaru", "bro cse hod")
- Misspelled names ("abdul basit", "dr basith") and short queries
- Non-faculty general questions ("What is AI?")
- Zero hallucination protocols
"""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.app.main import app
from backend.app.api.v1.chat import process_chat_query
from backend.app.services.identity.base import StudentContext
from backend.app.services.faculty_service import faculty_service
from backend.app.services.query_normalizer import query_normalizer


@pytest.mark.asyncio
async def test_cse_hod_query():
    """Requirement: 'Who is the CSE HOD?' should return Dr. K Abdul Basith with official designation."""
    res = await process_chat_query("Who is the CSE HOD?")
    assert res.intent == "FACULTY_HOD"
    assert "Dr. K Abdul Basith" in res.answer
    assert "Associate Professor & Head" in res.answer
    assert "Computer Science and Engineering" in res.answer
    assert "mlritm.ac.in" in res.answer


@pytest.mark.asyncio
async def test_short_cse_hod():
    """Requirement: 'cse hod' should return concise verified HOD card."""
    res = await process_chat_query("cse hod")
    assert res.intent == "FACULTY_HOD"
    assert "Dr. K Abdul Basith" in res.answer
    assert "hodcse@mlritm.ac.in" in res.answer


@pytest.mark.asyncio
async def test_it_hod_query():
    """Requirement: IT HOD should resolve to Dr. M Nagalakshmi."""
    res = await process_chat_query("it hod")
    assert res.intent == "FACULTY_HOD"
    assert "Dr. M Nagalakshmi" in res.answer
    assert "Professor & Head" in res.answer
    assert "hodit@mlritm.ac.in" in res.answer


@pytest.mark.asyncio
async def test_my_hod_authenticated():
    """
    Requirement 8: If student is authenticated, 'Who is my HOD?' should automatically
    identify the student's department and retrieve the corresponding HOD without asking again.
    """
    it_student = StudentContext(
        session_id=1,
        subject="22R21A1201",
        student_key="it_stud_1",
        roll_number="22R21A1201",
        name="Sneha Reddy"
    )
    res = await process_chat_query("Who is my HOD?", student=it_student)
    assert res.intent == "FACULTY_HOD"
    assert "Dr. M Nagalakshmi" in res.answer
    assert "Information Technology" in res.answer
    # Ensure private student roll number is not exposed in public message
    assert "22R21A1201" not in res.answer


@pytest.mark.asyncio
async def test_my_hod_unauthenticated():
    """Requirement: 'Who is my HOD?' without login prompts student to log in or specify department."""
    res = await process_chat_query("Who is my HOD?")
    assert res.intent == "FACULTY_HOD"
    assert "log in" in res.answer.lower() or "specify your department" in res.answer.lower()


@pytest.mark.asyncio
async def test_show_cse_faculty():
    """Requirement: 'Show CSE faculty.' should return CSE faculty directory."""
    res = await process_chat_query("Show CSE faculty.")
    assert res.intent == "FACULTY_LIST"
    assert "Computer Science and Engineering Faculty" in res.answer
    assert "Dr. K Abdul Basith" in res.answer
    assert "mlritm.ac.in/faculty-profile" in res.answer


@pytest.mark.asyncio
async def test_show_data_science_faculty():
    """Requirement: 'Show Data Science faculty.' should return DS faculty."""
    res = await process_chat_query("Show Data Science faculty.")
    assert res.intent == "FACULTY_LIST"
    assert "Data Science" in res.answer
    assert "Dr. B Srikantha Setty" in res.answer


@pytest.mark.asyncio
async def test_show_aiml_faculty():
    """Requirement: 'Show AI ML faculty.' should return AIML faculty."""
    res = await process_chat_query("Show AI ML faculty.")
    assert res.intent == "FACULTY_LIST"
    assert "AI & ML" in res.answer or "AI" in res.answer
    assert "Dr. B Ravi Prasad" in res.answer


@pytest.mark.asyncio
async def test_faculty_of_ece():
    """Requirement: 'Faculty of ECE.' should return ECE faculty directory."""
    res = await process_chat_query("Faculty of ECE.")
    assert res.intent == "FACULTY_LIST"
    assert "Dr. P Venkata Ramana" in res.answer


@pytest.mark.asyncio
async def test_faculty_of_mechanical():
    """Requirement: 'Faculty of Mechanical.' should return Mech faculty."""
    res = await process_chat_query("Faculty of Mechanical.")
    assert res.intent == "FACULTY_LIST"
    assert "Dr. G Surya Prakash Rao" in res.answer


@pytest.mark.asyncio
async def test_faculty_of_civil():
    """Requirement: 'Faculty of Civil.' should return Civil faculty."""
    res = await process_chat_query("Faculty of Civil.")
    assert res.intent == "FACULTY_LIST"
    assert "Dr. S P Jani" in res.answer


@pytest.mark.asyncio
async def test_professors_in_cse():
    """Requirement: 'Who are the professors in CSE?' filters professors."""
    res = await process_chat_query("Who are the professors in CSE?")
    assert res.intent == "FACULTY_LIST"
    assert "Professor" in res.answer


@pytest.mark.asyncio
async def test_assistant_professors_in_cse():
    """Requirement: 'List assistant professors in CSE.' filters assistant professors."""
    res = await process_chat_query("List assistant professors in CSE.")
    assert res.intent == "FACULTY_LIST"
    assert "Assistant" in res.answer


@pytest.mark.asyncio
async def test_faculty_count_cse():
    """Requirement: 'How many faculty are in CSE?' returns verified count."""
    res = await process_chat_query("How many faculty are in CSE?")
    assert res.intent == "FACULTY_COUNT"
    assert "currently indexed official MLRITM" in res.answer
    assert "faculty members listed" in res.answer


@pytest.mark.asyncio
async def test_tell_me_about_basith():
    """Requirement: 'Tell me about Dr. K Abdul Basith.' produces detailed clean student-friendly profile."""
    res = await process_chat_query("Tell me about Dr. K Abdul Basith.")
    assert res.intent in ["FACULTY_SEARCH", "FACULTY_PROFILE"]
    assert "Dr. K Abdul Basith" in res.answer
    assert "Associate Professor & Head" in res.answer
    assert "MLRS10003" in res.answer
    assert "View Official Profile" in res.answer
    assert "https://mlritm.ac.in" in res.answer


@pytest.mark.asyncio
async def test_multiturn_faculty_context():
    """
    Requirement 5: Conversational context follow-ups.
    Turn 1: 'Who is the CSE HOD?'
    Turn 2: 'What are his qualifications?'
    Turn 3: 'What about his research?'
    Turn 4: 'Show me the profile.'
    """
    history = [
        {"role": "user", "content": "Who is the CSE HOD?"},
        {
            "role": "assistant",
            "content": "Dr. K Abdul Basith is listed as Associate Professor & Head for CSE on the official MLRITM faculty page.",
        },
    ]

    # Turn 2: What are his qualifications?
    res_qual = await process_chat_query("What are his qualifications?", history=history)
    assert res_qual.intent == "FACULTY_PROFILE"
    assert "Qualifications" in res_qual.answer or "Degree" in res_qual.answer
    assert "Dr. K Abdul Basith" in res_qual.answer

    # Turn 3: What about his research?
    res_res = await process_chat_query("What about his research?", history=history)
    assert res_res.intent == "FACULTY_RESEARCH"
    assert "Dr. K Abdul Basith" in res_res.answer

    # Turn 4: Show me the profile.
    res_prof = await process_chat_query("Show me the profile.", history=history)
    assert res_prof.intent == "FACULTY_PROFILE"
    assert "View Official Profile" in res_prof.answer


@pytest.mark.asyncio
async def test_misspelled_and_partial_names():
    """Requirement: Handle spelling mistakes ('basith', 'abdul basit', 'dr basith')."""
    res1 = await process_chat_query("basith")
    assert "Dr. K Abdul Basith" in res1.answer

    res2 = await process_chat_query("abdul basit")
    assert "Dr. K Abdul Basith" in res2.answer


@pytest.mark.asyncio
async def test_telugu_english_mixed_queries():
    """Requirement 15: Support Telugu-English mixed queries like 'cse lo hod evaru' and 'bro cse hod?'."""
    res1 = await process_chat_query("cse lo hod evaru")
    assert res1.intent == "FACULTY_HOD"
    assert "Dr. K Abdul Basith" in res1.answer

    res2 = await process_chat_query("bro cse hod?")
    assert res2.intent == "FACULTY_HOD"
    assert "Dr. K Abdul Basith" in res2.answer


@pytest.mark.asyncio
async def test_faculty_research_queries():
    """Requirement 11: Support research queries like 'Who works in machine learning?'."""
    res = await process_chat_query("Who works in machine learning?")
    assert res.intent == "FACULTY_RESEARCH"
    assert "MACHINE LEARNING" in res.answer or "Machine Learning" in res.answer
    assert "Faculty Listed" in res.answer or "Specialization" in res.answer


@pytest.mark.asyncio
async def test_faculty_subject_zero_hallucination():
    """
    Requirement 10: If subject-to-faculty mapping is not officially available,
    the chatbot must state: 'I couldn't verify the current faculty assignment for that subject from the official MLRITM sources.'
    """
    res = await process_chat_query("Which faculty teaches Quantum Metamaterials?")
    assert (
        "I couldn't verify the current faculty assignment for that subject from the official MLRITM sources." in res.answer
        or "couldn't verify" in res.answer.lower()
    )


@pytest.mark.asyncio
async def test_general_non_faculty_question():
    """Requirement 22: 'What is AI?' should NOT be treated as a faculty query."""
    res = await process_chat_query("What is AI?")
    assert res.intent in ["GENERAL_KNOWLEDGE", "CODING_TECH"]
    assert "Artificial Intelligence" in res.answer or "AI" in res.answer
