"""
Standalone Test Runner for MLRITM Academic Advising Chatbot.
Executes all unit tests and reports pass/fail without requiring external pytest binary.
Discovers every test_* function in the modules listed below.
"""

import sys
import os
import importlib

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

TEST_MODULES = [
    "tests.test_calculator",
    "tests.test_security",
    "tests.test_identity",
    "tests.test_oidc",
    "tests.test_api_flow",
    "tests.test_personalization",
    "tests.test_anvaya_integration",
]


def run_all_tests():
    tests = []
    for module_name in TEST_MODULES:
        module = importlib.import_module(module_name)
        for name in dir(module):
            if name.startswith("test_") and callable(getattr(module, name)):
                tests.append((f"{module_name.split('.')[-1]}: {name}", getattr(module, name)))

    passed = 0
    failed = 0

    print("\n==========================================")
    print("Running MLRITM Advising Chatbot Test Suite")
    print("==========================================\n")

    for name, test_func in tests:
        try:
            test_func()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name} ({type(e).__name__}: {e})")
            failed += 1

    print("\n------------------------------------------")
    print(f"Results: {passed} Passed, {failed} Failed")
    print("------------------------------------------\n")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
