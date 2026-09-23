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
    assert any(name in res.answer for name in ["Dr Kummari Jayasri", "Dr. B Srikantha Setty", "Narasaiah", "Prasanna"])


@pytest.mark.asyncio
async def test_show_aiml_faculty():
    """Requirement: 'Show AI ML faculty.' should return AIML faculty."""
    res = await process_chat_query("Show AI ML faculty.")
    assert res.intent == "FACULTY_LIST"
    assert "AI & ML" in res.answer or "AI" in res.answer
    assert any(name in res.answer for name in ["Mr.G Mahendra Swaroop", "Dr. B Ravi Prasad", "AIML", "Faculty"])


@pytest.mark.asyncio
async def test_faculty_of_ece():
    """Requirement: 'Faculty of ECE.' should return ECE faculty directory."""
    res = await process_chat_query("Faculty of ECE.")
    assert res.intent == "FACULTY_LIST"
    assert any(name in res.answer for name in ["Dr. R Murali Prasad", "Dr. N Srinivas", "Dr. P Venkata Ramana", "Electronics and Communication"])


@pytest.mark.asyncio
async def test_faculty_of_mechanical():
    """Requirement: 'Faculty of Mechanical.' should return Mech faculty."""
    res = await process_chat_query("Faculty of Mechanical.")
    assert res.intent == "FACULTY_LIST"
    assert any(name in res.answer for name in ["Dr. G Surya Prakash Rao", "Mechanical Engineering", "Dr. U Sudhakar", "Dr. S P Jani"])


@pytest.mark.asyncio
async def test_faculty_of_civil():
    """Requirement: 'Faculty of Civil.' should return Civil faculty."""
    res = await process_chat_query("Faculty of Civil.")
    assert res.intent == "FACULTY_LIST"
    assert any(name in res.answer for name in ["Dr. K Murali", "Dr. S P Jani", "Civil Engineering"])


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


@pytest.mark.asyncio
async def test_authoritative_phone_directory_upgrades():
    """
    Verifies that faculty phone numbers and designations match
    authoritative values from https://mlritm.ac.in/Phone_Directory.
    """
    # 1. CSE HOD: Dr. K Abdul Basith | 9703242132
    res_cse = await process_chat_query("What is the phone number of CSE HOD?")
    assert res_cse.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "9703242132" in res_cse.answer
    assert "Abdul Basith" in res_cse.answer

    # 2. IT HOD: Dr. M Naga Lakshmi | 7036089991
    res_it = await process_chat_query("What is IT HOD phone number?")
    assert res_it.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "7036089991" in res_it.answer

    # 3. CSE Data Science HOD: Dr. A Arun Kumar | 9182367705
    res_ds = await process_chat_query("Who is the HOD of CSE Data Science?")
    assert res_ds.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "Arun Kumar" in res_ds.answer
    assert "9182367705" in res_ds.answer

    # 4. ECE HOD: Dr. N Srinivas | 9154334563
    res_ece = await process_chat_query("Who is the ECE HOD?")
    assert res_ece.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "Srinivas" in res_ece.answer
    assert "9154334563" in res_ece.answer

    # 5. Civil Engineering HOD: Dr. K Murali | 8074475825
    res_civil = await process_chat_query("Who is the Civil HOD?")
    assert res_civil.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "Murali" in res_civil.answer
    assert "8074475825" in res_civil.answer

    # 6. Mechanical Engineering HOD: Dr. U Sudhakar | 9912896727
    res_mech = await process_chat_query("Who is the Mechanical HOD?")
    assert res_mech.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "Sudhakar" in res_mech.answer
    assert "9912896727" in res_mech.answer

    # 7. EEE HOD: Dr. A Vinod | 8135817016
    res_eee = await process_chat_query("Who is the EEE HOD?")
    assert res_eee.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "Vinod" in res_eee.answer
    assert "8135817016" in res_eee.answer

    # 8. MBA HOD: Dr. K. Veeraiah | 9885650478
    res_mba = await process_chat_query("Who is the MBA HOD?")
    assert res_mba.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "Veeraiah" in res_mba.answer
    assert "9885650478" in res_mba.answer

    # 9. CSE-Cyber Security HOD: Dr. M Venkat Reddy | 9398564429
    res_cyber = await process_chat_query("Who is the Cyber Security HOD?")
    assert res_cyber.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "Venkat Reddy" in res_cyber.answer
    assert "9398564429" in res_cyber.answer

    # 10. CSE-AI-ML HOD: Dr. B Ravi Prasad | 9849356732
    res_aiml = await process_chat_query("Who is the AIML HOD?")
    assert res_aiml.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "Ravi Prasad" in res_aiml.answer
    assert "9849356732" in res_aiml.answer

    # 11. Freshman Engineering HOD: Dr. K Ashok | 8247516005
    res_fe = await process_chat_query("Who is the Freshman Engineering HOD?")
    assert res_fe.intent in ["FACULTY_HOD", "PHONE_DIRECTORY"]
    assert "Ashok" in res_fe.answer
    assert "8247516005" in res_fe.answer


@pytest.mark.asyncio
async def test_faculty_full_details_asish_adak():
    """
    Requirement: Typing a faculty name displays full details including:
    - faculty photo
    - Faculty ID
    - Total Experience
    - Undergraduate Degree
    - Postgraduate Degree
    - Ph.D Degree
    - Employment Status
    - Area of Specialization
    - Academic Identity
    - Video Lectures
    """
    res = await process_chat_query("Dr. Asish Adak")
    assert res.intent == "FACULTY_SEARCH"
    # 1. Faculty Photo
    assert "![" in res.answer
    assert "https://mlritm.ac.in" in res.answer
    # 2. Faculty ID
    assert "Faculty ID:" in res.answer
    assert "MLRS 10433" in res.answer
    # 3. Total Experience
    assert "Total Experience:" in res.answer
    # 4. Undergraduate Degree
    assert "Undergraduate Degree:" in res.answer
    assert "University of Calcutta" in res.answer
    # 5. Postgraduate Degree
    assert "Postgraduate Degree:" in res.answer
    assert "Rabindra Bharati" in res.answer
    # 6. Ph.D Degree
    assert "Ph.D Degree:" in res.answer
    assert "NIT Silchar" in res.answer
    # 7. Employment Status
    assert "Employment Status:" in res.answer
    assert "Full-Time" in res.answer
    # 8. Area of Specialization
    assert "Area of Specialization:" in res.answer
    assert "Biological System" in res.answer
    # 9. Academic Identity
    assert "Academic Identity:" in res.answer
    assert "https://mlritm.irins.org/profile/614444" in res.answer
    # 10. Video Lectures
    assert "Video Lectures:" in res.answer


@pytest.mark.asyncio
async def test_faculty_full_details_krishna_veni():
    """
    Requirement: Verifies YouTube video lectures and IRINS profile for Mrs V Krishna Veni.
    """
    res = await process_chat_query("Mrs V Krishna Veni")
    assert res.intent == "FACULTY_SEARCH"
    assert "![" in res.answer
    assert "MLRS10264" in res.answer
    assert "21 Years" in res.answer
    assert "Osmania University" in res.answer
    assert "E&Instrumentation" in res.answer
    assert "https://mlritm.irins.org/profile/255851" in res.answer
    assert "https://www.youtube.com/watch?v=_hJ71IAWx3o" in res.answer


@pytest.mark.asyncio
async def test_faculty_search_without_titles():
    """
    Requirement: Typing plain faculty name (lowercase, no Dr./Mrs.) displays full details.
    """
    res = await process_chat_query("asish adak")
    assert res.intent == "FACULTY_SEARCH"
    assert "Dr. Asish Adak" in res.answer
    assert "![" in res.answer
    assert "MLRS 10433" in res.answer
    assert "NIT Silchar" in res.answer


@pytest.mark.asyncio
async def test_faculty_search_by_faculty_id():
    """
    Requirement: Typing a faculty ID retrieves the corresponding faculty card.
    """
    res = await process_chat_query("MLRS 10433")
    assert res.intent == "FACULTY_SEARCH"
    assert "Dr. Asish Adak" in res.answer
    assert "![" in res.answer


