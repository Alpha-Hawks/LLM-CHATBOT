# MLRITM Student Master Data Pipeline

This directory contains the automated pipeline for extracting student master rolls from official MLRITM college PDFs, performing zero-loss auditing, and loading them idempotently into the chatbot database (`academic_chatbot.db`).

---

## 1. Pipeline Architecture

```
[PDFs in "students details mlritm\Neat pdfs"]
                       │
                       ▼
         [scripts/extract_students.py]
                       │
       ┌───────────────┴───────────────┐
       ▼                               ▼
[students_master.json / .csv]    [needs_review.csv]
       │                               │
       ├───────────────────────────────┘
       ▼
 [scripts/validate_and_report.py] ──> [validation_report.md]
       │
       ▼
  [scripts/import_students.py]
       │
       ▼
[academic_chatbot.db: students table]
       │
       ▼
[backend/app/services/student_service.py: getStudentByRollNumber()]
```

---

## 2. Re-running the Pipeline (When New PDFs Arrive)

### Step 1: Place New PDFs
Place new or updated PDFs into:
`C:\PROJECTSS\students details mlritm\Neat pdfs` (subfolders supported).

### Step 2: Run Extraction
```bash
python scripts/extract_students.py
```
This script:
- Scans all 22 PDF files using table extraction.
- Realigns shifted columns (such as the 100 Semester 5 Page 1 rows).
- Normalizes branches into canonical names matching JNTUH department codes (`01`=Civil, `02`=EEE, `03`=Mech, `04`=ECE, `05`=CSE, `12`=IT, `66`=AI&ML, `67`=Data Science).
- Derives `batch` (e.g. `2024`) from the student roll number prefix.
- Generates `students_master.csv`, `students_master.json`, and `needs_review.csv`.

### Step 3: Generate Validation & Audit Report
```bash
python scripts/validate_and_report.py
```
This script:
- Reconciles student counts against source PDFs.
- Audits sequence continuity and explains any missing roll numbers.
- Checks duplicates (0 allowed) and pattern integrity (`^[0-9]{2}7Y[15][A-Z][0-9]{2}[0-9A-Z]{2}$`).
- Generates `validation_report.md`.

### Step 4: Import into SQLite Database
```bash
# Import all records (including review flags on flagged rows)
python scripts/import_students.py --mode all

# Alternatively, import only verified clean records (skipping review rows):
python scripts/import_students.py --mode verified-only
```
This script:
- Performs an idempotent `INSERT ... ON CONFLICT(roll_number) DO UPDATE SET ...` upsert.
- Commits transactions in batches (default: 250 records).
- Re-running the script produces 0 duplicate records.
- Reconciles database row counts overall, per-branch, and per-batch.

---

## 3. Python Lookup API

The chatbot backend queries student records using `backend.app.services.student_service`:

```python
from backend.app.services.student_service import getStudentByRollNumber, getStudentByRollNumberSync

# Async lookup:
student = await getStudentByRollNumber("247Y1A0101")
if student == "not found":
    print("Student not found")
else:
    print(student["student_name"], student["branch"], student["batch"])

# Synchronous lookup (for background / non-async code):
student = getStudentByRollNumberSync("247Y1A0101")
```

---

## 4. Security & Privacy Safeguards

1. **No Public Listing**: The API provides no public endpoints to enumerate or list student records.
2. **Server-Side Credentials**: Records are read exclusively by server-side services.
3. **Session-Scoped Self-Read**: When students sign in via the Anvaya portal, authorization gates (`ensure_can_read_records` and `verify_record_owner`) enforce that students can query only their own profile.
4. **Git Protection**: `.gitignore` strictly blocks all extracted student files (`students_master.csv`, `students_master.json`, `needs_review.csv`, `students_clean.csv`, `*.db`) from being committed to source control.
