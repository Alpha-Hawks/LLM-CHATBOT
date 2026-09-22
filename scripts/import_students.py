"""
Idempotent Student Database Import Script.

Imports verified student master records from `students_master.json` into
the official `students` table in `academic_chatbot.db`.

Features:
- Idempotent upsert by roll_number (no duplicates on re-run)
- Batched transaction commits
- Supports `--verified-only` or `--all` (with needs_review flags attached)
- Comprehensive reconciliation audit (overall, per-branch, per-batch)
"""

import os
import sys
import json
import csv
import argparse
import sqlite3
from datetime import datetime
from collections import Counter

# Ensure root directory is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def import_students(
    mode: str = "all",
    batch_size: int = 250,
    db_path: str = None
):
    if db_path is None:
        db_path = os.path.join(PROJECT_ROOT, "academic_chatbot.db")

    json_path = os.path.join(PROJECT_ROOT, "students_master.json")
    review_path = os.path.join(PROJECT_ROOT, "needs_review.csv")

    if not os.path.exists(json_path):
        print(f"Error: Master file not found at {json_path}")
        sys.exit(1)

    print(f"Loading student master data from: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        master_records = json.load(f)

    # Load review flags mapping: roll_number -> flag_reason
    review_map = {}
    if os.path.exists(review_path):
        with open(review_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                review_map[row["roll_number"].strip().upper()] = row["flag_reason"]

    total_master = len(master_records)
    print(f"Loaded {total_master:,} records from master dataset.")
    print(f"Loaded {len(review_map):,} review flags from needs_review.csv.")

    # Filter according to mode
    records_to_import = []
    for r in master_records:
        clean_roll = r["roll_number"].strip().upper()
        is_flagged = clean_roll in review_map
        reason = review_map.get(clean_roll, None)

        if mode == "verified-only" and is_flagged:
            continue

        record_copy = dict(r)
        record_copy["roll_number"] = clean_roll
        record_copy["needs_review"] = 1 if is_flagged else 0
        record_copy["review_reason"] = reason
        records_to_import.append(record_copy)

    print(f"Import mode: [{mode.upper()}]. Selected {len(records_to_import):,} records for database import.")

    # Connect to SQLite
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Ensure table exists
    cur.execute("""
    CREATE TABLE IF NOT EXISTS students (
        roll_number VARCHAR(20) PRIMARY KEY,
        student_name VARCHAR(255) NOT NULL,
        name VARCHAR(255),
        batch VARCHAR(10),
        admission_batch VARCHAR(10),
        entry_type VARCHAR(20),
        year VARCHAR(20),
        section VARCHAR(10),
        academic_year VARCHAR(50),
        gender VARCHAR(20),
        date_of_birth VARCHAR(20),
        student_mobile VARCHAR(30),
        student_email VARCHAR(255),
        email VARCHAR(255),
        father_name VARCHAR(255),
        father_mobile VARCHAR(30),
        mother_name VARCHAR(255),
        mother_mobile VARCHAR(30),
        branch VARCHAR(100),
        admission_year VARCHAR(20),
        scholarship_type VARCHAR(100),
        parent_income VARCHAR(50),
        parent_profession VARCHAR(100),
        admission_category VARCHAR(50),
        caste_name VARCHAR(100),
        current_semester VARCHAR(50),
        source_file VARCHAR(255),
        source_page INTEGER,
        source_row INTEGER,
        extraction_method VARCHAR(50),
        needs_review BOOLEAN DEFAULT 0 NOT NULL,
        review_reason TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
    );
    """)
    conn.commit()

    # Upsert SQL query
    upsert_sql = """
    INSERT INTO students (
        roll_number, student_name, name, batch, admission_batch, entry_type, year,
        section, academic_year, gender, date_of_birth,
        student_mobile, student_email, email, father_name, father_mobile, mother_name, mother_mobile,
        branch, admission_year, scholarship_type, parent_income, parent_profession,
        admission_category, caste_name, current_semester, source_file, source_page, source_row,
        extraction_method, needs_review, review_reason, created_at, updated_at
    ) VALUES (
        :roll_number, :student_name, :name, :batch, :admission_batch, :entry_type, :year,
        :section, :academic_year, :gender, :date_of_birth,
        :student_mobile, :student_email, :email, :father_name, :father_mobile, :mother_name, :mother_mobile,
        :branch, :admission_year, :scholarship_type, :parent_income, :parent_profession,
        :admission_category, :caste_name, :current_semester, :source_file, :source_page, :source_row,
        :extraction_method, :needs_review, :review_reason, :now, :now
    )
    ON CONFLICT(roll_number) DO UPDATE SET
        student_name = excluded.student_name,
        name = excluded.name,
        batch = excluded.batch,
        admission_batch = excluded.admission_batch,
        entry_type = excluded.entry_type,
        year = excluded.year,
        section = excluded.section,
        academic_year = excluded.academic_year,
        gender = excluded.gender,
        date_of_birth = excluded.date_of_birth,
        student_mobile = excluded.student_mobile,
        student_email = excluded.student_email,
        email = excluded.email,
        father_name = excluded.father_name,
        father_mobile = excluded.father_mobile,
        mother_name = excluded.mother_name,
        mother_mobile = excluded.mother_mobile,
        branch = excluded.branch,
        admission_year = excluded.admission_year,
        scholarship_type = excluded.scholarship_type,
        parent_income = excluded.parent_income,
        parent_profession = excluded.parent_profession,
        admission_category = excluded.admission_category,
        caste_name = excluded.caste_name,
        current_semester = excluded.current_semester,
        source_file = excluded.source_file,
        source_page = excluded.source_page,
        source_row = excluded.source_row,
        extraction_method = excluded.extraction_method,
        needs_review = excluded.needs_review,
        review_reason = excluded.review_reason,
        updated_at = excluded.updated_at;
    """

    now_str = datetime.utcnow().isoformat()
    inserted_count = 0

    # Batched execution
    print(f"Beginning batched upsert in chunks of {batch_size}...")
    for i in range(0, len(records_to_import), batch_size):
        chunk = records_to_import[i : i + batch_size]
        params_chunk = []
        for r in chunk:
            s_name = r.get("student_name", "")
            s_email = r.get("student_email", "")
            s_batch = r.get("batch", "")
            clean_roll = r["roll_number"]
            e_type = "Regular (1A)" if len(clean_roll) >= 6 and clean_roll[4:6] == "1A" else "Lateral Entry (5A)" if len(clean_roll) >= 6 and clean_roll[4:6] == "5A" else ""
            params_chunk.append({
                "roll_number": clean_roll,
                "student_name": s_name,
                "name": s_name,
                "batch": s_batch,
                "admission_batch": s_batch,
                "entry_type": e_type,
                "year": r.get("admission_year", ""),
                "section": r.get("section", ""),
                "academic_year": r.get("academic_year", ""),
                "gender": r.get("gender", ""),
                "date_of_birth": r.get("date_of_birth", ""),
                "student_mobile": r.get("student_mobile", ""),
                "student_email": s_email,
                "email": s_email,
                "father_name": r.get("father_name", ""),
                "father_mobile": r.get("father_mobile", ""),
                "mother_name": r.get("mother_name", ""),
                "mother_mobile": r.get("mother_mobile", ""),
                "branch": r.get("branch", ""),
                "admission_year": r.get("admission_year", ""),
                "scholarship_type": r.get("scholarship_type", ""),
                "parent_income": r.get("parent_income", ""),
                "parent_profession": r.get("parent_profession", ""),
                "admission_category": r.get("admission_category", ""),
                "caste_name": r.get("caste_name", ""),
                "current_semester": r.get("current_semester", ""),
                "source_file": r.get("source_file", ""),
                "source_page": r.get("source_page", None),
                "source_row": r.get("source_row", None),
                "extraction_method": r.get("extraction_method", ""),
                "needs_review": r.get("needs_review", 0),
                "review_reason": r.get("review_reason", None),
                "now": now_str
            })

        cur.executemany(upsert_sql, params_chunk)
        conn.commit()
        inserted_count += len(chunk)
        print(f"  Committed {inserted_count:,} / {len(records_to_import):,} records...")

    print("Batched import completed successfully!")

    # =========================================================================
    # RECONCILIATION AUDIT
    # =========================================================================
    print("\n" + "=" * 60)
    print("DATABASE RECONCILIATION AUDIT")
    print("=" * 60)

    cur.execute("SELECT COUNT(*) FROM students")
    db_total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM students WHERE needs_review = 0")
    db_clean = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM students WHERE needs_review = 1")
    db_flagged = cur.fetchone()[0]

    print(f"Total Rows in DB:       {db_total:,} (Expected: {len(records_to_import):,})")
    print(f"  - Verified Clean:    {db_clean:,}")
    print(f"  - Flagged for Review:{db_flagged:,}")

    # Branch counts reconciliation
    print("\n--- Breakdown By Branch ---")
    cur.execute("SELECT branch, COUNT(*) FROM students GROUP BY branch ORDER BY COUNT(*) DESC")
    db_branches = cur.fetchall()
    expected_branch_counts = Counter(r["branch"] for r in records_to_import)

    print(f"{'Branch':<48} | {'DB Count':<10} | {'Expected':<10} | {'Match':<6}")
    print("-" * 78)
    all_branches_match = True
    for branch_name, db_cnt in db_branches:
        exp_cnt = expected_branch_counts.get(branch_name, 0)
        match_str = "YES" if db_cnt == exp_cnt else "NO"
        if db_cnt != exp_cnt:
            all_branches_match = False
        print(f"{branch_name:<48} | {db_cnt:<10} | {exp_cnt:<10} | {match_str:<6}")

    # Batch counts reconciliation
    print("\n--- Breakdown By Batch ---")
    cur.execute("SELECT batch, COUNT(*) FROM students GROUP BY batch ORDER BY batch ASC")
    db_batches = cur.fetchall()
    expected_batch_counts = Counter(r["batch"] for r in records_to_import)

    print(f"{'Batch':<15} | {'DB Count':<10} | {'Expected':<10} | {'Match':<6}")
    print("-" * 45)
    all_batches_match = True
    for batch_name, db_cnt in db_batches:
        exp_cnt = expected_batch_counts.get(batch_name, 0)
        match_str = "YES" if db_cnt == exp_cnt else "NO"
        if db_cnt != exp_cnt:
            all_batches_match = False
        print(f"{batch_name:<15} | {db_cnt:<10} | {exp_cnt:<10} | {match_str:<6}")

    conn.close()

    print("\n" + "=" * 60)
    if db_total == len(records_to_import) and all_branches_match and all_batches_match:
        print("RECONCILIATION SUCCESSFUL: 100% database count match across all cohorts!")
    else:
        print("RECONCILIATION DISCREPANCY DETECTED!")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import student records into chatbot database.")
    parser.add_argument(
        "--mode",
        choices=["all", "verified-only"],
        default="all",
        help="Import all records (with needs_review flags) or verified-only (default: all)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=250,
        help="Batch commit size (default: 250)"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database"
    )
    args = parser.parse_args()

    import_students(mode=args.mode, batch_size=args.batch_size, db_path=args.db_path)
