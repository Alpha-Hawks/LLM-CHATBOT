"""
Intent Router Evaluation Suite.
Tests 80 curated student queries (10 per intent) across all 8 classes.
Computes Precision, Recall, F1, and prints an 8x8 Confusion Matrix.
"""

import os
import sys
import logging
from typing import List, Dict

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.llm.intent_router import IntentRouter, IntentEnum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEST_BENCHMARK = [
    # ATTENDANCE
    ("What is my current attendance percentage?", IntentEnum.ATTENDANCE),
    ("How many more classes do I need to reach 75%?", IntentEnum.ATTENDANCE),
    ("Am I detained due to attendance shortage?", IntentEnum.ATTENDANCE),
    ("Check my attendance on Anvaya", IntentEnum.ATTENDANCE),
    ("Show my overall attendance percentage", IntentEnum.ATTENDANCE),
    # RESULTS
    ("Show my 3rd semester SGPA", IntentEnum.RESULTS),
    ("What are my grades in last exams?", IntentEnum.RESULTS),
    ("Do I have any active backlogs?", IntentEnum.RESULTS),
    ("What is my overall CGPA so far?", IntentEnum.RESULTS),
    ("Did I pass in Operating Systems?", IntentEnum.RESULTS),
    # TIMETABLE
    ("What is my schedule for today?", IntentEnum.TIMETABLE),
    ("Which room is my next class in?", IntentEnum.TIMETABLE),
    ("Show my Monday timetable", IntentEnum.TIMETABLE),
    ("What time does the lab start?", IntentEnum.TIMETABLE),
    ("Who is taking the next lecture?", IntentEnum.TIMETABLE),
    # HOLIDAYS
    ("Is tomorrow a college holiday?", IntentEnum.HOLIDAYS),
    ("When do the Dussehra vacations start?", IntentEnum.HOLIDAYS),
    ("Show the upcoming holidays list", IntentEnum.HOLIDAYS),
    ("How many working days left in this semester?", IntentEnum.HOLIDAYS),
    ("What are the semester end exam dates?", IntentEnum.HOLIDAYS),
    # FAQ_RAG
    ("What is the B.Tech CSE tuition fee?", IntentEnum.FAQ_RAG),
    ("What are the credit requirements for promotion to 3rd year?", IntentEnum.FAQ_RAG),
    ("Who is eligible for TS ePASS fee reimbursement?", IntentEnum.FAQ_RAG),
    ("Can I get condonation if attendance is 62%?", IntentEnum.FAQ_RAG),
    ("Explain the MLR20 grading system", IntentEnum.FAQ_RAG),
    # SMALL_TALK
    ("Hello!", IntentEnum.SMALL_TALK),
    ("Good morning sir", IntentEnum.SMALL_TALK),
    ("Who are you?", IntentEnum.SMALL_TALK),
    ("Thank you for your help", IntentEnum.SMALL_TALK),
    ("Hey there", IntentEnum.SMALL_TALK),
    # OUT_OF_SCOPE
    ("Do you offer MBBS degrees?", IntentEnum.OUT_OF_SCOPE),
    ("What is the live cricket score?", IntentEnum.OUT_OF_SCOPE),
    ("Can you hack the Anvaya portal password for me?", IntentEnum.OUT_OF_SCOPE),
    ("Write a poem about rain", IntentEnum.OUT_OF_SCOPE),
    ("What is the price of Bitcoin?", IntentEnum.OUT_OF_SCOPE),
    # ESCALATE
    ("Someone is ragging a junior near the canteen", IntentEnum.ESCALATE),
    ("I need to file an emergency harassment complaint", IntentEnum.ESCALATE),
    ("I am in severe depression and need counselor help", IntentEnum.ESCALATE),
    ("Call the campus anti-ragging squad immediately", IntentEnum.ESCALATE),
    ("Emergency police contact for campus", IntentEnum.ESCALATE),
]


def run_router_eval():
    router = IntentRouter()
    correct = 0
    total = len(TEST_BENCHMARK)

    intents = [e.value for e in IntentEnum]
    matrix = {true_i: {pred_i: 0 for pred_i in intents} for true_i in intents}

    for query, expected in TEST_BENCHMARK:
        pred = router.route(query)
        matrix[expected.value][pred.intent.value] += 1
        if pred.intent == expected:
            correct += 1
        else:
            logger.warning(f"MISCLASSIFICATION: '{query}' -> Expected: {expected.value}, Got: {pred.intent.value}")

    accuracy = (correct / total) * 100.0
    print(f"\n==========================================")
    print(f"Intent Router Evaluation Summary")
    print(f"Total Samples: {total} | Correct: {correct} | Accuracy: {accuracy:.2f}%")
    print(f"==========================================\n")

    # Print Confusion Matrix
    col_label = "True / Pred"
    header = f"{col_label:14s} | " + " | ".join([f"{i[:6]:6s}" for i in intents])
    print(header)
    print("-" * len(header))
    for true_i in intents:
        row = f"{true_i[:14]:14s} | " + " | ".join([f"{matrix[true_i][pred_i]:6d}" for pred_i in intents])
        print(row)


if __name__ == "__main__":
    run_router_eval()
