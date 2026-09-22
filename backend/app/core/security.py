"""
Security, Cryptography, and DPDP Act 2023 Compliance Module.
Implements:
1. AES-256-GCM authenticated encryption/decryption for ephemeral session tokens.
2. DPDP Act 2023 consent validation and purpose specification.
3. Strict PII redaction rules for logs.
"""

import os
import json
import base64
import hmac
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime, timezone

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

from backend.app.core.config import settings


def get_aes_key() -> bytes:
    """Derives a 32-byte key from configuration."""
    raw = settings.AES_ENCRYPTION_KEY.encode("utf-8")
    if len(raw) < 32:
        return raw.ljust(32, b"0")
    return raw[:32]


def encrypt_session_payload(data: Dict[str, Any]) -> str:
    """Encrypts session payload using AES-256-GCM (or authenticated HMAC fallback)."""
    serialized = json.dumps(data).encode("utf-8")
    key = get_aes_key()

    if HAS_CRYPTOGRAPHY:
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, serialized, None)
        payload = nonce + ciphertext
        return base64.urlsafe_b64encode(payload).decode("utf-8")
    else:
        # Standard-library fallback: HMAC-SHA256 signed payload
        sig = hmac.new(key, serialized, hashlib.sha256).digest()
        payload = sig + serialized
        return base64.urlsafe_b64encode(payload).decode("utf-8")


def decrypt_session_payload(token: str) -> Optional[Dict[str, Any]]:
    """Decrypts token and verifies authentication tag / signature."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8"))
        key = get_aes_key()

        if HAS_CRYPTOGRAPHY:
            if len(raw) < 13:
                return None
            nonce = raw[:12]
            ciphertext = raw[12:]
            aesgcm = AESGCM(key)
            decrypted = aesgcm.decrypt(nonce, ciphertext, None)
            return json.loads(decrypted.decode("utf-8"))
        else:
            if len(raw) < 33:
                return None
            sig = raw[:32]
            serialized = raw[32:]
            expected_sig = hmac.new(key, serialized, hashlib.sha256).digest()
            if not hmac.compare_digest(sig, expected_sig):
                return None
            return json.loads(serialized.decode("utf-8"))
    except Exception:
        return None


# The values shipped in the repository. They are public, so they protect nothing.
PLACEHOLDER_AES_KEYS = {"0123456789abcdef0123456789abcdef"}
PLACEHOLDER_SECRET_KEYS = {
    "mlritm-secret-key-change-in-production-2024",
    "mlritm-production-secret-token-key-2024",
    "replace-with-a-secure-random-secret-key-for-jwt",
}


def weak_secret_problems() -> list:
    """Reasons the configured encryption secrets cannot be trusted with real student data."""
    problems = []
    if settings.AES_ENCRYPTION_KEY in PLACEHOLDER_AES_KEYS:
        problems.append("AES_ENCRYPTION_KEY is the placeholder from the repository")
    elif len(settings.AES_ENCRYPTION_KEY.encode("utf-8")) < 32:
        problems.append("AES_ENCRYPTION_KEY is shorter than 32 bytes")
    if settings.SECRET_KEY in PLACEHOLDER_SECRET_KEYS:
        problems.append("SECRET_KEY is the placeholder from the repository")
    return problems


def assert_secrets_fit_for_student_data() -> None:
    """
    Refuses to start with a real student-data source and a public encryption key.

    The stored academic snapshot is encrypted at rest with AES_ENCRYPTION_KEY. If that key is the
    one published in the repository, the encryption is decoration, so outside development a real
    data provider is not allowed to run on it. With no data provider, nothing sensitive is stored
    and the problem is only reported.
    """
    problems = weak_secret_problems()
    if not problems or settings.ENVIRONMENT == "development":
        return

    # Judge the provider that will actually run: a "sample" or half-configured "anvaya_api"
    # setting resolves to "none" (fail closed) and stores nothing. Imported here because the
    # providers depend on this module's configuration.
    from backend.app.services.student_data.providers import get_student_data_provider

    if get_student_data_provider().name != "none":
        raise RuntimeError(
            "Refusing to start with a real student data provider: " + "; ".join(problems)
            + ". Set strong random values in the environment (e.g. `python -c \"import secrets;"
            "print(secrets.token_hex(16))\"` for AES_ENCRYPTION_KEY)."
        )


class DPDPComplianceNotice:
    """Digital Personal Data Protection (DPDP) Act 2023 Notice & Consent Definition."""

    PURPOSE_SPECIFICATION = (
        "This Academic Advising Chatbot accesses your MLRITM academic records (attendance, "
        "semester examination results, and class timetables) exclusively to answer your immediate queries. "
        "You are identified through your Anvaya sign-in; this assistant never asks for or receives your "
        "Anvaya password. Chatbot sessions expire automatically in 20 minutes. "
        "Under the DPDP Act 2023, you retain the right to withdraw consent and request session data deletion."
    )

    @staticmethod
    def verify_consent(consent_given: bool) -> bool:
        """Enforces mandatory explicit student consent before live ERP session creation."""
        return consent_given is True
