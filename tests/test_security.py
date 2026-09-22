"""
Unit Tests for Cryptography & DPDP Act 2023 Compliance.
"""

try:
    import pytest
except ImportError:
    pytest = None
from backend.app.core.security import encrypt_session_payload, decrypt_session_payload, DPDPComplianceNotice


def test_aes_gcm_encrypt_decrypt_roundtrip():
    data = {
        "raw_token": "anvaya_session_12345",
        "roll_number": "21R21A0501",
        "expires_at": "2026-09-19T20:00:00"
    }

    encrypted = encrypt_session_payload(data)
    assert isinstance(encrypted, str)
    assert encrypted != ""

    decrypted = decrypt_session_payload(encrypted)
    assert decrypted is not None
    assert decrypted["raw_token"] == data["raw_token"]
    assert decrypted["roll_number"] == data["roll_number"]


def test_decrypt_corrupted_token_returns_none():
    corrupted = "bad_token_not_valid_base64_or_ciphertext"
    assert decrypt_session_payload(corrupted) is None


def test_dpdp_consent_verification():
    assert DPDPComplianceNotice.verify_consent(True) is True
    assert DPDPComplianceNotice.verify_consent(False) is False
