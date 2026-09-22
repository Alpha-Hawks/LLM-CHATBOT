"""
Structured Logging with Automatic PII Redaction.
Redacts passwords, tokens, phone numbers, and sensitive identifiers from audit logs.
"""

import re
import logging


class PIIRedactingFormatter(logging.Formatter):
    PATTERNS = [
        (re.compile(r'("?password"?\s*[:=]\s*)"[^"]+"', re.IGNORECASE), r'\1"[REDACTED]"'),
        (re.compile(r'("?session_token"?\s*[:=]\s*)"[^"]+"', re.IGNORECASE), r'\1"[REDACTED_TOKEN]"'),
        (re.compile(r'("?token"?\s*[:=]\s*)"[^"]+"', re.IGNORECASE), r'\1"[REDACTED_TOKEN]"'),
        (re.compile(r'\b\d{10}\b'), '[REDACTED_PHONE]'),
    ]

    def format(self, record: logging.LogRecord) -> str:
        orig = super().format(record)
        for pattern, repl in self.PATTERNS:
            orig = pattern.sub(repl, orig)
        return orig


def setup_secure_logging():
    logger = logging.getLogger("mlritm_advising_bot")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = PIIRedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


logger = setup_secure_logging()
