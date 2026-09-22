import os
import sys
import glob
import re
import json
import csv
from collections import Counter, defaultdict
import pdfplumber

folder = r"C:\PROJECTSS\students details mlritm\Neat pdfs"
pdf_files = sorted(glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True))

roll_regex = re.compile(r"^([0-9]{2})(7Y)([15][A-Z])([0-9]{2})([0-9A-Z]{2})$")

BRANCH_MAP = {
    "01": "CIVIL ENGINEERING",
    "02": "ELECTRICAL AND ELECTRONICS ENGINEERING",
    "03": "MECHANICAL ENGINEERING",
    "04": "ELECTRONICS AND COMMUNICATION ENGINEERING",
    "05": "COMPUTER SCIENCE AND ENGINEERING",
    "12": "INFORMATION TECHNOLOGY",
    "66": "COMPUTER SCIENCE AND ENGINEERING (AI & ML)",
    "67": "COMPUTER SCIENCE AND ENGINEERING (DATA SCIENCE)"
}

fieldnames = [
    "roll_number",
    "student_name",
    "batch",
    "section",
    "academic_year",
    "gender",
    "date_of_birth",
    "student_mobile",
    "student_email",
    "father_name",
    "father_mobile",
    "mother_name",
    "mother_mobile",
    "branch",
    "admission_year",
    "scholarship_type",
    "parent_income",
    "parent_profession",
    "admission_category",
    "caste_name",
    "current_semester",
    "source_file",
    "source_page",
    "source_row",
    "extraction_method"
]

def clean_cell(c):
    return re.sub(r'\s+', ' ', c).strip() if c else ""

def get_clean_branch(b_raw, clean_roll):
    br_code = clean_roll[6:8]
    if br_code in BRANCH_MAP:
        return BRANCH_MAP[br_code]
    return clean_cell(b_raw).upper()

all_students = []
needs_review = []
file_stats = []

print("Extracting and validating all 22 PDFs...")

for pdf_path in pdf_files:
    fname = os.path.relpath(pdf_path, folder)
    is_it = ("information_technology" in fname.lower())
    
    file_records = 0
    with pdfplumber.open(pdf_path) as pdf:
        num_pages = len(pdf.pages)
        for page_idx, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            for t in tables:
                if not t:
                    continue
                for r_idx, raw_row in enumerate(t):
                    if not raw_row or not any(raw_row):
                        continue
                    row = [clean_cell(cell) for cell in raw_row]
                    first_cell = row[0].upper()
                    if "ROLL" in first_cell or "STUDENT" in first_cell or "S.NO" in first_cell or "ACADEMIC" in first_cell:
                        continue
                    clean_roll = re.sub(r'\s+', '', row[0]).upper()
                    if not clean_roll:
                        continue
                        
                    is_shifted = False
                    batch_year = f"20{clean_roll[:2]}"
                    branch_clean = get_clean_branch(row[11] if len(row) > 11 else "", clean_roll)
                    
                    if is_it:
                        rec = {
                            "roll_number": clean_roll,
                            "student_name": row[1] if len(row) > 1 else "",
                            "batch": batch_year,
                            "section": "",
                            "academic_year": row[2] if len(row) > 2 else "",
                            "gender": row[3] if len(row) > 3 else "",
                            "date_of_birth": row[4] if len(row) > 4 else "",
                            "student_mobile": row[5] if len(row) > 5 else "",
                            "student_email": row[6] if len(row) > 6 else "",
                            "father_name": "",
                            "father_mobile": row[7] if len(row) > 7 else "",
                            "mother_name": row[8] if len(row) > 8 else "",
                            "mother_mobile": row[9] if len(row) > 9 else "",
                            "branch": branch_clean,
                            "admission_year": row[11] if len(row) > 11 else "",
                            "scholarship_type": row[12] if len(row) > 12 else "",
                            "parent_income": row[13] if len(row) > 13 else "",
                            "parent_profession": row[14] if len(row) > 14 else "",
                            "admission_category": row[15] if len(row) > 15 else "",
                            "caste_name": row[16] if len(row) > 16 else "",
                            "current_semester": row[17] if len(row) > 17 else "",
                            "source_file": fname,
                            "source_page": page_idx,
                            "source_row": r_idx,
                            "extraction_method": "text_table"
                        }
                    else:
                        # Check if row is shifted (Page 1 of sem 5 files where col 11 is empty and col 12 is Branch)
                        if len(row) >= 19 and row[11] == "" and any(b in clean_cell(row[12]).upper() for b in ["ENGINEER", "SCIENCE", "CIVIL", "COMPUTER", "ELECTRONIC", "ELECTRICAL", "MECHANIC"]):
                            is_shifted = True
                            rec = {
                                "roll_number": clean_roll,
                                "student_name": row[1],
                                "batch": batch_year,
                                "section": "",
                                "academic_year": row[2],
                                "gender": row[3],
                                "date_of_birth": row[4],
                                "student_mobile": row[5],
                                "student_email": row[6],
                                "father_name": row[7],
                                "father_mobile": row[9],   # Realigned from col 9
                                "mother_name": row[10],    # Realigned from col 10
                                "mother_mobile": "",       # Blank in source table
                                "branch": branch_clean,    # Realigned & canonical
                                "admission_year": batch_year, # Inferred from roll batch
                                "scholarship_type": row[13],
                                "parent_income": row[14],
                                "parent_profession": row[15],
                                "admission_category": row[16],
                                "caste_name": row[17],
                                "current_semester": row[18],
                                "source_file": fname,
                                "source_page": page_idx,
                                "source_row": r_idx,
                                "extraction_method": "text_table_realigned"
                            }
                        else:
                            rec = {
                                "roll_number": clean_roll,
                                "student_name": row[1] if len(row) > 1 else "",
                                "batch": batch_year,
                                "section": "",
                                "academic_year": row[2] if len(row) > 2 else "",
                                "gender": row[3] if len(row) > 3 else "",
                                "date_of_birth": row[4] if len(row) > 4 else "",
                                "student_mobile": row[5] if len(row) > 5 else "",
                                "student_email": row[6] if len(row) > 6 else "",
                                "father_name": row[7] if len(row) > 7 else "",
                                "father_mobile": row[8] if len(row) > 8 else "",
                                "mother_name": row[9] if len(row) > 9 else "",
                                "mother_mobile": row[10] if len(row) > 10 else "",
                                "branch": branch_clean,
                                "admission_year": row[12] if len(row) > 12 else "",
                                "scholarship_type": row[13] if len(row) > 13 else "",
                                "parent_income": row[14] if len(row) > 14 else "",
                                "parent_profession": row[15] if len(row) > 15 else "",
                                "admission_category": row[16] if len(row) > 16 else "",
                                "caste_name": row[17] if len(row) > 17 else "",
                                "current_semester": row[18] if len(row) > 18 else "",
                                "source_file": fname,
                                "source_page": page_idx,
                                "source_row": r_idx,
                                "extraction_method": "text_table"
                            }

                    # Data quality checks for needs_review
                    flags = []
                    if is_shifted:
                        flags.append("Shifted table columns on P1: Realigned Father Mobile, Mother Name, Branch; Admission Year from roll")
                    if not rec["student_name"]:
                        flags.append("Missing student name")
                    if any(ch.isdigit() for ch in rec["student_name"]):
                        flags.append(f"Student name contains digits: {rec['student_name']}")
                    if not roll_regex.match(clean_roll):
                        flags.append(f"Roll format irregular: {clean_roll}")
                    if rec["mother_name"].isdigit():
                        flags.append(f"Mother Name contains digits in source PDF: {rec['mother_name']}")
                    
                    mob = rec["student_mobile"]
                    if mob and (len(mob) != 10 or not mob.isdigit()):
                        flags.append(f"Unusual student mobile: {mob}")
                        
                    dob = rec["date_of_birth"]
                    if dob and not (len(dob) == 10 and dob[4] == '-' and dob[7] == '-'):
                        flags.append(f"Unusual DOB format: {dob}")
                        
                    if flags:
                        needs_review.append({
                            "roll_number": clean_roll,
                            "student_name": rec["student_name"],
                            "source_file": fname,
                            "source_page": page_idx,
                            "source_row": r_idx,
                            "flag_reason": "; ".join(flags),
                            "details": json.dumps({k: v for k, v in rec.items() if k in ("branch", "date_of_birth", "student_mobile", "student_email", "father_mobile", "mother_name", "admission_year")})
                        })

                    all_students.append(rec)
                    file_records += 1
                    
    file_stats.append({
        "file": fname,
        "pages": num_pages,
        "records": file_records
    })

print(f"Total students extracted: {len(all_students)}")
print(f"Total rows flagged for review: {len(needs_review)}")

# Save students_master.csv
out_csv_path = r"c:\PROJECTSS\LLM CHATBOT\students_master.csv"
with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(all_students)
print(f"Saved: {out_csv_path}")

# Save students_master.json
out_json_path = r"c:\PROJECTSS\LLM CHATBOT\students_master.json"
with open(out_json_path, "w", encoding="utf-8") as f:
    json.dump(all_students, f, indent=2, ensure_ascii=False)
print(f"Saved: {out_json_path}")

# Save needs_review.csv
out_review_path = r"c:\PROJECTSS\LLM CHATBOT\needs_review.csv"
review_fieldnames = ["roll_number", "student_name", "source_file", "source_page", "source_row", "flag_reason", "details"]
with open(out_review_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=review_fieldnames)
    writer.writeheader()
    writer.writerows(needs_review)
print(f"Saved: {out_review_path}")

# Breakdown metrics
batch_counts = Counter(s['roll_number'][:2] for s in all_students)
branch_name_counts = Counter(s['branch'] for s in all_students)

print("\nBatch Counts:")
for b, c in sorted(batch_counts.items()):
    print(f"  Batch 20{b}: {c} students")

print("\nBranch Counts:")
for br, c in branch_name_counts.most_common():
    print(f"  {br}: {c} students")
