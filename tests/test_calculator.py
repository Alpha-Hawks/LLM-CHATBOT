"""
Unit Tests for Academic Calculator Engine.
Validates attendance percentages, shortage alerts, classes needed, and safe bunks.
"""

try:
    import pytest
except ImportError:
    pytest = None
from backend.app.services.academic_calculator import calculate_attendance_metrics, calculate_cgpa


def test_attendance_above_75_is_safe():
    # 80 attended out of 100 conducted = 80.0%
    metrics = calculate_attendance_metrics(attended=80, conducted=100)
    assert metrics["percentage"] == 80.0
    assert metrics["is_shortage"] is False
    assert metrics["condonation_eligible"] is False
    assert metrics["detention_risk"] is False
    assert metrics["classes_needed_for_75"] == 0
    # Safe bunks: (80 - 0.75*100) / 0.75 = 5 / 0.75 = 6.66 -> 6 classes
    assert metrics["max_bunks_permitted"] == 6


def test_attendance_condonation_range():
    # 70 attended out of 100 conducted = 70.0% (between 65% and 75%)
    metrics = calculate_attendance_metrics(attended=70, conducted=100)
    assert metrics["percentage"] == 70.0
    assert metrics["is_shortage"] is True
    assert metrics["condonation_eligible"] is True
    assert metrics["detention_risk"] is False
    # Classes needed: (0.75*100 - 70) / (1 - 0.75) = 5 / 0.25 = 20 classes
    assert metrics["classes_needed_for_75"] == 20
    assert metrics["max_bunks_permitted"] == 0


def test_attendance_critical_detention():
    # 60 attended out of 100 conducted = 60.0% (< 65%)
    metrics = calculate_attendance_metrics(attended=60, conducted=100)
    assert metrics["percentage"] == 60.0
    assert metrics["is_shortage"] is True
    assert metrics["condonation_eligible"] is False
    assert metrics["detention_risk"] is True
    # Classes needed: (0.75*100 - 60) / 0.25 = 15 / 0.25 = 60 classes
    assert metrics["classes_needed_for_75"] == 60


def test_calculate_cgpa():
    sgpas = [8.5, 8.0, 9.0, 8.5]
    cgpa = calculate_cgpa(sgpas)
    assert cgpa == 8.5
