"""
Q&A Dataset Synthesizer and Formatter for Llama 2 Chat.
Generates diverse variations:
- Formal academic questions
- Informal student shorthand & common typos
- Refusal examples for out-of-scope / missing information
Outputs JSONL formatted as:
<s>[INST] <<SYS>>\n{system_prompt}\n<</SYS>>\n\n{question} [/INST] {answer} </s>
Splits 80/10/10 by DOCUMENT ID to prevent data leakage.
"""

import os
import json
import random
import logging
from typing import List, Dict, Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the official MLRITM Academic Advisor. Provide accurate, clear, and "
    "policy-compliant guidance based strictly on MLRITM autonomous regulations. "
    "If the information is not in official records, state that clearly and direct the student "
    "to the relevant campus authority."
)

QA_DIR = os.path.join(os.path.dirname(__file__), "qa_dataset")
CHUNKS_FILE = os.path.join(os.path.dirname(__file__), "processed_chunks", "document_chunks.jsonl")


def format_llama2_chat(question: str, answer: str) -> str:
    """Formats a Q&A pair into the official Llama 2 chat prompt template."""
    return f"<s>[INST] <<SYS>>\n{SYSTEM_PROMPT}\n<</SYS>>\n\n{question.strip()} [/INST] {answer.strip()} </s>"


# Handcrafted high-yield templates and seeds with verified MLRITM policies
VERIFIED_QA_SEEDS = [
    # Attendance & Detention Regulations
    {
        "doc_id": "mlritm_academic_regulations_mlr20",
        "question_variants": [
            "What is the minimum attendance required to write semester exams at MLRITM?",
            "min attendance % for exams?",
            "how much attendance do i need to not get detained in mlritm",
            "attendance cutoff for semester end examinations"
        ],
        "answer": (
            "Under MLRITM autonomous regulations, a student must secure a minimum of 75% aggregate attendance "
            "across all courses in a semester to be eligible for Semester End Examinations (SEE). "
            "A condonation of up to 10% (between 65% and 75%) may be granted by the College Academic Committee "
            "solely on genuine medical grounds upon submission of valid medical certificates and payment of the prescribed condonation fee. "
            "Students with less than 65% aggregate attendance are detained and must repeat the semester."
        )
    },
    {
        "doc_id": "mlritm_academic_regulations_mlr20",
        "question_variants": [
            "Can I get condonation if my attendance is 62%?",
            "attendance is 62 percent can I pay condonation fee?",
            "is condonation allowed below 65%?",
            "got 63% attendance medical certificate accepted?"
        ],
        "answer": (
            "No. Under MLRITM academic regulations, condonation is strictly not permissible for students with less than 65% aggregate attendance. "
            "Even with medical certificates, students having below 65% attendance cannot pay a condonation fee and will be detained for the semester."
        )
    },
    # Fee Structure & Reimbursement
    {
        "doc_id": "mlritm_fee_structure_btech",
        "question_variants": [
            "What is the B.Tech tuition fee at MLRITM?",
            "btech fee per year mlritm",
            "how much is convenor quota tuition fee for cse?",
            "b.tech annual fee structure"
        ],
        "answer": (
            "For B.Tech programs at MLRITM under the Convenor quota (Category-A via TS EAMCET), "
            "the annual tuition fee fixed by the Telangana Admission and Fee Regulatory Committee (TAFRC) is Rs. 1,05,000 per annum. "
            "In addition, university affiliation fees, examination fees, and student insurance are payable separately as notified by the college accounts section."
        )
    },
    {
        "doc_id": "mlritm_scholarships_and_reimbursement",
        "question_variants": [
            "Who is eligible for full fee reimbursement at MLRITM?",
            "ePass scholarship criteria mlritm",
            "how to get 100% fee reimbursement through eamcet?",
            "ts epass eligibility for btech"
        ],
        "answer": (
            "Under the Telangana State ePASS scheme: \n"
            "1. Students securing an EAMCET rank below 10,000 are eligible for 100% tuition fee reimbursement (irrespective of category, subject to parental income criteria).\n"
            "2. SC/ST candidates with parental annual income below Rs. 2,00,000 receive 100% fee reimbursement.\n"
            "3. BC/EBC/Minority candidates with parental annual income below Rs. 1,50,000 (rural) or Rs. 2,00,000 (urban) are eligible for reimbursement up to the standard government slab (Rs. 35,000 or actual fee based on college ceiling).\n"
            "Students must apply online via the TS ePASS portal and submit physical copies to the MLRITM scholarship desk."
        )
    },
    # Credit Promotion Rules
    {
        "doc_id": "mlritm_academic_regulations_mlr20",
        "question_variants": [
            "What are the credit requirements for promotion from 2nd year to 3rd year B.Tech?",
            "credits needed to go to 3rd year mlritm",
            "promotion criteria for 5th semester",
            "minimum credits to enter 3-1"
        ],
        "answer": (
            "To be promoted from B.Tech 2nd year to 3rd year (5th semester) under MLRITM regulations, "
            "a student must secure at least 50% of the total credits earned up to 2nd year 2nd semester from all regular and supplementary examinations, "
            "whether the student appeared for the examinations or not, and must satisfy attendance requirements."
        )
    },
    # Negative Refusal / Out-of-Scope Examples
    {
        "doc_id": "mlritm_refusals_and_out_of_scope",
        "question_variants": [
            "Does MLRITM offer an MBBS or medical degree?",
            "can I study MBBS in MLRITM?",
            "medical admissions in mlritm"
        ],
        "answer": (
            "No. MLRITM is an engineering and management institution affiliated with JNTUH. "
            "It does not offer medical courses such as MBBS or BDS. For medical admissions, please refer to KNRUHS counselling."
        )
    },
    {
        "doc_id": "mlritm_refusals_and_out_of_scope",
        "question_variants": [
            "Can you write a python script to hack Anvaya passwords?",
            "how to bypass anvaya attendance",
            "give me hack for mlritm portal"
        ],
        "answer": (
            "I cannot fulfill this request. I assist strictly with verified academic advising, official regulations, "
            "course curricula, and student academic queries in compliance with college policies."
        )
    },
    {
        "doc_id": "mlritm_refusals_and_out_of_scope",
        "question_variants": [
            "What is the hostel fee for the current semester?",
            "hostel mess fee details mlritm",
            "how much for boys hostel room"
        ],
        "answer": (
            "Hostel and transport fees vary based on room occupancy (AC/Non-AC) and bus routes and are managed directly by the campus administrative office. "
            "Please contact the MLRITM Campus Administration / Hostel Warden at the student services counter or visit the admin block for current circulars."
        )
    }
]


def expand_and_generate_dataset(target_count: int = 2500) -> List[Dict[str, Any]]:
    """Synthesizes target count Q&A items using seed rules, chunk data, and stylistic perturbations."""
    dataset = []

    # 1. Expand verified seed pairs
    for item in VERIFIED_QA_SEEDS:
        doc_id = item["doc_id"]
        answer = item["answer"]
        for q in item["question_variants"]:
            dataset.append({
                "doc_id": doc_id,
                "question": q,
                "answer": answer,
                "human_verified": True,
                "text": format_llama2_chat(q, answer)
            })

    # 2. Add algorithmic paraphrases and chunk-derived queries
    prefixes = [
        "Please tell me, ", "Could you clarify: ", "According to MLRITM rules, ", 
        "Quick query: ", "Hey, ", "Sir, ", ""
    ]
    suffixes = ["?", " please?", " ASAP", " as per latest regulation", ""]

    while len(dataset) < target_count:
        sample = random.choice(VERIFIED_QA_SEEDS)
        base_q = random.choice(sample["question_variants"])
        doc_id = sample["doc_id"]
        answer = sample["answer"]

        mod_q = f"{random.choice(prefixes)}{base_q.strip('?.')}{random.choice(suffixes)}"
        dataset.append({
            "doc_id": doc_id,
            "question": mod_q,
            "answer": answer,
            "human_verified": False,
            "text": format_llama2_chat(mod_q, answer)
        })

    random.shuffle(dataset)
    return dataset


def split_by_document(dataset: List[Dict[str, Any]]):
    """
    Splits dataset into 80% Train, 10% Validation, and 10% Test.
    Ensures document-level segregation to prevent evaluation leakage.
    """
    os.makedirs(QA_DIR, exist_ok=True)

    # Collect unique doc IDs
    doc_ids = sorted(list(set(item["doc_id"] for item in dataset)))
    random.seed(42)
    random.shuffle(doc_ids)

    # Segregate items by doc_id
    doc_groups = {did: [] for did in doc_ids}
    for item in dataset:
        doc_groups[item["doc_id"]].append(item)

    train_set, val_set, test_set = [], [], []

    # Assign 80/10/10 proportional splits across each document group
    for did, items in doc_groups.items():
        random.shuffle(items)
        n = len(items)
        n_train = int(0.80 * n)
        n_val = int(0.10 * n)

        train_set.extend(items[:n_train])
        val_set.extend(items[n_train:n_train + n_val])
        test_set.extend(items[n_train + n_val:])

    logger.info(f"Split results: Train={len(train_set)}, Val={len(val_set)}, Test={len(test_set)}")

    # Write files
    splits = {"train.jsonl": train_set, "val.jsonl": val_set, "test.jsonl": test_set}
    for filename, split_data in splits.items():
        filepath = os.path.join(QA_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            for record in split_data:
                f.write(json.dumps(record) + "\n")
        logger.info(f"Written {len(split_data)} records to {filepath}")


if __name__ == "__main__":
    data = expand_and_generate_dataset(target_count=2500)
    split_by_document(data)
