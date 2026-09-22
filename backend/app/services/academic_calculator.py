"""
Academic Calculations Engine.
Deterministic mathematical evaluations for:
1. Attendance percentage and condonation eligibility.
2. Minimum classes required to reach 75% threshold.
3. Maximum classes that can be safely missed without dropping below 75%.
4. CGPA and SGPA aggregations.
Never delegates numerical calculations to LLM to prevent hallucination.
"""

import math
from typing import Dict, Any


def calculate_attendance_metrics(attended: int, conducted: int, target_pct: float = 75.0) -> Dict[str, Any]:
    """
    Computes attendance percentage, shortage flag, required classes, and safe bunks.
    Target percentage is 75.0% per MLRITM autonomous regulations.
    """
    if conducted == 0:
        return {
            "percentage": 100.0,
            "is_shortage": False,
            "condonation_eligible": False,
            "detention_risk": False,
            "classes_needed_for_75": 0,
            "max_bunks_permitted": 0
        }

    pct = round((attended / conducted) * 100.0, 2)
    is_shortage = pct < target_pct
    condonation_eligible = (65.0 <= pct < 75.0)
    detention_risk = pct < 65.0

    # Classes needed to reach target_pct:
    # (attended + x) / (conducted + x) >= target_pct / 100
    # attended + x >= (target_pct / 100) * conducted + (target_pct / 100) * x
    # x * (1 - target_pct / 100) >= (target_pct / 100) * conducted - attended
    # x >= ((target_pct / 100) * conducted - attended) / (1 - target_pct / 100)
    classes_needed = 0
    if is_shortage:
        target_ratio = target_pct / 100.0
        numerator = (target_ratio * conducted) - attended
        denominator = 1.0 - target_ratio
        classes_needed = max(0, math.ceil(numerator / denominator))

    # Safe bunks before dropping below target_pct:
    # attended / (conducted + y) >= target_pct / 100
    # attended >= (target_pct / 100) * conducted + (target_pct / 100) * y
    # y <= (attended - (target_pct / 100) * conducted) / (target_pct / 100)
    max_bunks = 0
    if not is_shortage:
        target_ratio = target_pct / 100.0
        numerator = attended - (target_ratio * conducted)
        max_bunks = max(0, math.floor(numerator / target_ratio))

    return {
        "percentage": pct,
        "is_shortage": is_shortage,
        "condonation_eligible": condonation_eligible,
        "detention_risk": detention_risk,
        "classes_needed_for_75": classes_needed,
        "max_bunks_permitted": max_bunks
    }


def calculate_cgpa(semester_sgpas: list) -> float:
    """Computes cumulative grade point average (CGPA) from semester SGPAs."""
    if not semester_sgpas:
        return 0.0
    return round(sum(semester_sgpas) / len(semester_sgpas), 2)


if __name__ == "__main__":
    test1 = calculate_attendance_metrics(attended=120, conducted=170)
    print("Test Shortage (120/170):", test1)

    test2 = calculate_attendance_metrics(attended=150, conducted=170)
    print("Test Safe (150/170):", test2)
