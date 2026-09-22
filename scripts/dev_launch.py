"""
Development-only Anvaya Launch Simulator.

Mints the kind of short-lived signed launch token that Anvaya would send when a signed-in
student opens the MLRITM AI Assistant, and prints the launch URL to open in a browser.
It exists only to exercise the signed-launch integration locally; it is NOT a way for a
student to choose an identity:
- refuses to run unless ENVIRONMENT=development
- only works with an HMAC secret that exists solely in your local .env
- in production the verification key is Anvaya's (ideally a public key, which cannot sign)

Usage:
    python scripts/dev_launch.py [--subject dev-student-001] [--roll DEV-0001] [--name "Dev Student"]
"""

import argparse
import os
import sys
import time
import uuid
from urllib.parse import urlencode

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import jwt  # noqa: E402
from backend.app.core.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Development-only Anvaya launch simulator")
    parser.add_argument("--subject", default="dev-student-237y1a1270", help="sub claim (Anvaya's user id)")
    parser.add_argument("--roll", default="237Y1A1270", help="roll number claim, if IDENTITY_ROLL_NUMBER_CLAIM is set")
    parser.add_argument("--name", default="Student 237Y1A1270", help="name claim")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="chatbot server")
    args = parser.parse_args()

    if settings.ENVIRONMENT != "development":
        print("Refusing to run: ENVIRONMENT is not 'development'.", file=sys.stderr)
        return 1
    if settings.IDENTITY_PROVIDER != "signed_launch":
        print("Set IDENTITY_PROVIDER=signed_launch in .env to use the launch simulator.", file=sys.stderr)
        return 1
    algorithm = settings.LAUNCH_TOKEN_ALGORITHMS.split(",")[0].strip()
    if not algorithm.startswith("HS") or len(settings.LAUNCH_TOKEN_HMAC_SECRET.encode("utf-8")) < 32:
        print("The simulator needs LAUNCH_TOKEN_ALGORITHMS=HS256 and a LAUNCH_TOKEN_HMAC_SECRET of 32+ bytes.", file=sys.stderr)
        return 1

    now = int(time.time())
    claims = {
        "iss": settings.LAUNCH_TOKEN_ISSUER,
        "aud": settings.LAUNCH_TOKEN_AUDIENCE,
        "sub": args.subject,
        "iat": now,
        "exp": now + min(120, settings.LAUNCH_TOKEN_MAX_AGE_SECONDS),
        "jti": str(uuid.uuid4()),
        settings.IDENTITY_NAME_CLAIM or "name": args.name,
    }
    if settings.IDENTITY_ROLL_NUMBER_CLAIM:
        claims[settings.IDENTITY_ROLL_NUMBER_CLAIM] = args.roll
    if settings.IDENTITY_ROLE_CLAIM:
        allowed = [v.strip() for v in settings.IDENTITY_STUDENT_ROLE_VALUES.split(",") if v.strip()]
        claims[settings.IDENTITY_ROLE_CLAIM] = allowed[0] if allowed else "student"

    token = jwt.encode(claims, settings.LAUNCH_TOKEN_HMAC_SECRET, algorithm=algorithm)
    print(f"{args.base_url.rstrip('/')}{settings.API_V1_STR}/auth/launch?{urlencode({'launch_token': token})}")
    print("Open within 2 minutes; the link works once.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
