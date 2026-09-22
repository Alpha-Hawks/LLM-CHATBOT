"""
Idempotent Student Record Synchronization Service.
Transfers and synchronizes all student master records from source JSON/CSV into academic_chatbot.db.
Maintains zero data loss, avoids duplicates, and reports complete audit metrics.
"""

import os
import json
import sqlite3
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "academic_chatbot.db"))
SOURCE_JSON = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "students_master.json"))


def sync_student_records(source_path: str = SOURCE_JSON, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Safely and idempotently synchronizes source records into the chatbot SQLite database.
    
    Returns audit metrics:
      - total_source_records
      - total_transferred
      - total_already_existing
      - total_updated
      - total_newly_inserted
      - total_failed
      - total_duplicates_detected
      - final_db_count
      - verified_equal
    """
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source file not found: {source_path}")
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file not found: {db_path}")

    with open(source_path, "r", encoding="utf-8") as f:
        source_records = json.load(f)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Query existing students
    cur.execute("SELECT * FROM students")
    existing_rows = {row["roll_number"].strip().upper(): dict(row) for row in cur.fetchall()}

    seen_rolls = set()
    total_source = len(source_records)
    already_existing = 0
    updated = 0
    newly_inserted = 0
    failed = 0
    duplicates_detected = 0

    for rec in source_records:
        raw_roll = rec.get("roll_number")
        if not raw_roll or not str(raw_roll).strip():
            failed += 1
            continue

        clean_roll = str(raw_roll).strip().upper()

        if clean_roll in seen_rolls:
            duplicates_detected += 1
            continue
        seen_rolls.add(clean_roll)

        s_name = (rec.get("student_name") or "").strip()
        batch = (rec.get("batch") or "").strip()
        section = (rec.get("section") or "").strip()
        acad_yr = (rec.get("academic_year") or "").strip()
        gender = (rec.get("gender") or "").strip()
        dob = (rec.get("date_of_birth") or "").strip()
        s_mobile = (rec.get("student_mobile") or "").strip()
        s_email = (rec.get("student_email") or "").strip()
        f_name = (rec.get("father_name") or "").strip()
        f_mobile = (rec.get("father_mobile") or "").strip()
        m_name = (rec.get("mother_name") or "").strip()
        m_mobile = (rec.get("mother_mobile") or "").strip()
        branch = (rec.get("branch") or "").strip()
        adm_year = (rec.get("admission_year") or "").strip()
        scholarship = (rec.get("scholarship_type") or "").strip()
        p_income = (rec.get("parent_income") or "").strip()
        p_prof = (rec.get("parent_profession") or "").strip()
        adm_cat = (rec.get("admission_category") or "").strip()
        caste = (rec.get("caste_name") or "").strip()
        curr_sem = (rec.get("current_semester") or "").strip()
        src_file = (rec.get("source_file") or "").strip()
        src_page = rec.get("source_page")
        src_row = rec.get("source_row")
        extract_meth = (rec.get("extraction_method") or "").strip()
        needs_rev = 1 if rec.get("needs_review") else 0
        rev_reason = rec.get("review_reason")

        # Derive entry_type if missing
        entry_type = "Lateral Entry (5A)" if "5A" in clean_roll else "Regular (1A)"

        sem_to_year = {
            "BT2601": "1st Year",
            "BT2503": "2nd Year",
            "BT2405": "3rd Year",
            "BT2307": "4th Year",
        }
        year_of_study = sem_to_year.get(curr_sem, adm_year or "Not Available")

        if clean_roll in existing_rows:
            already_existing += 1
            existing = existing_rows[clean_roll]
            diff = False
            fields_to_check = {
                "student_name": s_name,
                "name": s_name,
                "batch": batch,
                "admission_batch": batch,
                "section": section,
                "academic_year": acad_yr,
                "gender": gender,
                "date_of_birth": dob,
                "student_mobile": s_mobile,
                "student_email": s_email,
                "email": s_email,
                "father_name": f_name,
                "father_mobile": f_mobile,
                "mother_name": m_name,
                "mother_mobile": m_mobile,
                "branch": branch,
                "admission_year": adm_year,
                "year": year_of_study,
                "scholarship_type": scholarship,
                "parent_income": p_income,
                "parent_profession": p_prof,
                "admission_category": adm_cat,
                "caste_name": caste,
                "current_semester": curr_sem,
                "source_file": src_file,
                "entry_type": entry_type,
            }
            for col, val in fields_to_check.items():
                if str(existing.get(col) or "").strip() != val:
                    diff = True
                    break

            if diff:
                cur.execute("""
                    UPDATE students SET
                        student_name = ?, name = ?, batch = ?, admission_batch = ?,
                        entry_type = ?, year = ?, section = ?, academic_year = ?,
                        gender = ?, date_of_birth = ?, student_mobile = ?,
                        student_email = ?, email = ?, father_name = ?, father_mobile = ?,
                        mother_name = ?, mother_mobile = ?, branch = ?, admission_year = ?,
                        scholarship_type = ?, parent_income = ?, parent_profession = ?,
                        admission_category = ?, caste_name = ?, current_semester = ?,
                        source_file = ?, source_page = ?, source_row = ?, extraction_method = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE roll_number = ?
                """, (
                    s_name, s_name, batch, batch,
                    entry_type, year_of_study, section, acad_yr,
                    gender, dob, s_mobile,
                    s_email, s_email, f_name, f_mobile,
                    m_name, m_mobile, branch, adm_year,
                    scholarship, p_income, p_prof,
                    adm_cat, caste, curr_sem,
                    src_file, src_page, src_row, extract_meth,
                    clean_roll
                ))
                updated += 1
        else:
            cur.execute("""
                INSERT INTO students (
                    roll_number, student_name, name, batch, admission_batch,
                    entry_type, year, section, academic_year,
                    gender, date_of_birth, student_mobile,
                    student_email, email, father_name, father_mobile,
                    mother_name, mother_mobile, branch, admission_year,
                    scholarship_type, parent_income, parent_profession,
                    admission_category, caste_name, current_semester,
                    source_file, source_page, source_row, extraction_method,
                    needs_review, review_reason, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """, (
                clean_roll, s_name, s_name, batch, batch,
                entry_type, year_of_study, section, acad_yr,
                gender, dob, s_mobile,
                s_email, s_email, f_name, f_mobile,
                m_name, m_mobile, branch, adm_year,
                scholarship, p_income, p_prof,
                adm_cat, caste, curr_sem,
                src_file, src_page, src_row, extract_meth,
                needs_rev, rev_reason
            ))
            newly_inserted += 1

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM students")
    final_db_count = cur.fetchone()[0]
    conn.close()

    total_transferred = updated + newly_inserted

    report = {
        "total_source_records": total_source,
        "total_transferred": total_transferred,
        "total_already_existing": already_existing,
        "total_updated": updated,
        "total_newly_inserted": newly_inserted,
        "total_failed": failed,
        "total_duplicates_detected": duplicates_detected,
        "final_db_count": final_db_count,
        "verified_equal": total_source == final_db_count
    }
    return report


if __name__ == "__main__":
    rep = sync_student_records()
    print("Synchronization & Audit Report:")
    for k, v in rep.items():
        print(f"  {k}: {v}")
