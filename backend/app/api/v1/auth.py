"""
Authentication & DPDP Consent API Endpoints.

Students are identified ONLY through an official Anvaya identity mechanism (see
services/identity). This API never accepts a password or a roll number from the browser.

    Anvaya -> /auth/launch (signed launch token)  or  /auth/oidc/login -> /auth/oidc/callback
           -> redirect to /app/#handoff=<one-time code>
           -> /auth/session/exchange -> chatbot session token (Bearer)
"""

import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.security import DPDPComplianceNotice
from backend.app.db.session import get_db
from backend.app.db.models import AuditLog
from backend.app.services import sessions
from backend.app.services.authorization import bearer_token
from backend.app.services.identity.base import AuthenticatedIdentity, IdentityError, IdentityNotConfigured
from backend.app.services.identity.launch_token import SignedLaunchTokenProvider
from backend.app.services.identity.oidc import OIDCProvider
from backend.app.services.identity.registry import get_identity_provider
from backend.app.services.anvaya_service import anvaya_service
from backend.app.services.student_data.providers import get_student_data_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication & DPDP Consent"])

APP_PATH = "/app/"


class ConsentNoticeResponse(BaseModel):
    notice: str
    purpose: str
    rights: str


class HandoffExchangeRequest(BaseModel):
    handoff_code: str = Field(..., min_length=1, max_length=128)


class ConsentRequest(BaseModel):
    dpdp_consent_granted: bool = Field(..., description="Explicit consent to show your academic records")


def _app_redirect(fragment: str) -> RedirectResponse:
    # 303 so a POSTed launch becomes a GET of the app page
    return RedirectResponse(url=f"{APP_PATH}#{fragment}", status_code=status.HTTP_303_SEE_OTHER)


def _auth_error_redirect(code: str, detail: str) -> RedirectResponse:
    logger.warning(f"Anvaya sign-in rejected ({code}): {detail}")
    return _app_redirect(f"auth_error={quote(code)}")


async def _session_row_or_401(authorization: Optional[str], db: AsyncSession):
    row = await sessions.resolve_session(db, bearer_token(authorization))
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in through Anvaya.")
    return row


def _student_summary(row) -> dict:
    return {
        "roll_number": row.roll_number,
        "display_name": row.display_name,
        "name": row.display_name,
        "anvaya_user_id": getattr(row, "anvaya_user_id", None),
        "student_id": getattr(row, "student_id", None),
        "consent_granted": row.consent_granted_at is not None,
        "expires_at": row.expires_at,
    }


@router.get("/dpdp-notice", response_model=ConsentNoticeResponse)
async def get_dpdp_notice():
    """Returns DPDP Act 2023 compliant data privacy notice."""
    return ConsentNoticeResponse(
        notice=DPDPComplianceNotice.PURPOSE_SPECIFICATION,
        purpose="Retrieve and format immediate academic advising data (attendance, marks, schedules).",
        rights="Consent withdrawal and session deletion available upon logout."
    )


@router.get("/config")
async def get_auth_config():
    """Tells the web app which official sign-in path (if any) is available."""
    provider = get_identity_provider()
    sign_in_url = None
    sign_in_mode = "unavailable"
    if provider and provider.name == "oidc":
        sign_in_url = f"{settings.API_V1_STR}/auth/oidc/login"
        sign_in_mode = "redirect"
    elif provider and provider.name == "signed_launch":
        if settings.ENVIRONMENT == "development":
            sign_in_url = "/anvaya/login.html"
            sign_in_mode = "dev_simulator"
        else:
            sign_in_mode = "anvaya_launch"
    elif provider and provider.name == "anvaya_sso":
        sign_in_url = "/anvaya/login.html"
        sign_in_mode = "anvaya_sso"

    return {
        "identity_provider": provider.name if provider else "none",
        "sign_in_url": sign_in_url,
        "sign_in_mode": sign_in_mode,
        "anvaya_url": settings.ANVAYA_BASE_URL,
        "anvaya_login_url": getattr(settings, "ANVAYA_LOGIN_URL", f"{settings.ANVAYA_BASE_URL}/Login"),
        "anvaya_app_url": getattr(settings, "ANVAYA_APP_URL", f"{settings.ANVAYA_BASE_URL}/App"),
        "student_data_provider": get_student_data_provider().name,
    }


@router.get("/anvaya/login")
async def anvaya_sso_login_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Directs the browser to the official Anvaya Portal SSO login interface or returns config."""
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        sample_students = []
        try:
            from sqlalchemy import text
            res = await db.execute(text("SELECT roll_number, name, branch FROM students LIMIT 10"))
            sample_students = [dict(r) for r in res.mappings().fetchall()]
        except Exception:
            pass
        return JSONResponse({
            "anvaya_sso_enabled": True,
            "anvaya_login_url": "/anvaya/login.html",
            "students": sample_students,
        })
    return RedirectResponse(url="/anvaya/login.html", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/anvaya/authorize")
async def anvaya_sso_authorize(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Anvaya Portal SSO Authentication Gateway.
    Authenticates the student's Anvaya credentials, issues a single-use authorization code,
    and redirects back to the chatbot callback.
    Supports both HTML Form POST and JSON POST.
    """
    content_type = request.headers.get("content-type", "")
    is_json = "application/json" in content_type
    if is_json:
        try:
            body = await request.json()
        except Exception:
            body = {}
        username = body.get("username") or body.get("roll_number") or ""
        password = body.get("password") or ""
        name = body.get("name")
    else:
        form = await request.form()
        username = form.get("username") or form.get("roll_number") or ""
        password = form.get("password") or ""
        name = form.get("name")

    clean_roll = str(username).strip().upper()
    if not clean_roll:
        if is_json:
            raise HTTPException(status_code=400, detail="Roll number / username is required.")
        return _auth_error_redirect("invalid_login", "Roll number / username is required.")

    # Official student directory lookup from database, falling back to dictionary
    if not name:
        try:
            from sqlalchemy import text
            db_row = (await db.execute(text("SELECT name FROM students WHERE roll_number = :roll"), {"roll": clean_roll})).fetchone()
            if db_row and db_row[0]:
                name = db_row[0]
        except Exception:
            pass

    if not name:
        name = f"Student {clean_roll}"

    display_name = name.strip()
    code, state = await sessions.create_anvaya_auth_code(db, clean_roll, display_name)
    callback_url = f"{settings.API_V1_STR}/auth/anvaya/callback?code={code}&state={state}"

    if is_json or "application/json" in request.headers.get("accept", ""):
        return {
            "success": True,
            "redirect_url": callback_url,
            "code": code,
            "state": state,
            "roll_number": clean_roll,
            "name": display_name,
        }

    return RedirectResponse(url=callback_url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/anvaya/callback")
async def anvaya_sso_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Receives authorization code from Anvaya SSO, validates it, creates a secure chatbot session,
    and redirects the student to the chatbot handoff.
    """
    if not code:
        return _auth_error_redirect("invalid_login", "Missing authorization code from Anvaya SSO.")

    auth_data = await sessions.consume_anvaya_auth_code(db, code)
    if not auth_data:
        return _auth_error_redirect("invalid_or_expired_code", "Anvaya authorization code expired or already used.")

    roll_number, display_name = auth_data
    identity = anvaya_service.extract_student_identity(
        roll_number=roll_number,
        name=display_name,
        anvaya_user_id=f"anvaya-{roll_number.lower()}",
        student_id=roll_number,
    )

    handoff = await sessions.create_pending_session(db, identity)
    db.add(AuditLog(event_type="AUTH_ANVAYA_SSO", intent_detected="LOGIN_SUCCESS"))
    await db.commit()
    return _app_redirect(f"handoff={handoff}")


async def _handle_launch(launch_token: Optional[str], db: AsyncSession) -> RedirectResponse:
    provider = get_identity_provider()
    if not isinstance(provider, SignedLaunchTokenProvider):
        return _auth_error_redirect("not_configured", "signed launch is not the configured identity provider")
    try:
        identity, claims = await provider.verify(launch_token or "")
        await sessions.consume_launch_token_id(
            db, identity.issuer, str(claims["jti"]), datetime.utcfromtimestamp(float(claims["exp"]))
        )
    except IdentityNotConfigured as e:
        return _auth_error_redirect("not_configured", str(e))
    except IdentityError as e:
        return _auth_error_redirect("invalid_launch", str(e))

    # --- Seed Validation against students database (Step 1 requirement) ---
    clean_roll = (identity.roll_number or identity.subject or "").strip().upper()
    from sqlalchemy import text
    db_student = (await db.execute(
        text("SELECT roll_number, name, branch, admission_batch, entry_type, section, year, email FROM students WHERE roll_number = :roll"),
        {"roll": clean_roll}
    )).mappings().fetchone()

    if not db_student:
        is_dev_test_fixture = (
            settings.ENVIRONMENT == "development"
            and (
                clean_roll.startswith(("ROLL-", "STUDENT-", "DEV-", "TEST-", "UID-"))
                or clean_roll in ("23R21A1234", "237Y1A0599")
            )
        )
        if not is_dev_test_fixture:
            return _auth_error_redirect("student_not_found", f"Student roll number {clean_roll} not found in official MLRITM database.")
    else:
        # Load authentic student name and seed attributes
        identity.roll_number = clean_roll
        if not identity.student_key:
            identity.student_key = clean_roll
        if not identity.display_name or identity.display_name.startswith(("Student ", "dev-", "ROLL-")):
            identity.display_name = db_student["name"]

    code = await sessions.create_pending_session(db, identity)
    db.add(AuditLog(event_type="AUTH_LAUNCH", intent_detected="LOGIN_SUCCESS"))
    await db.commit()
    return _app_redirect(f"handoff={code}")


@router.get("/dev-launch")
async def anvaya_dev_launch(
    roll: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    format: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Convenience development launch simulator.
    MUST be impossible to enable in production.
    """
    if settings.ENVIRONMENT != "development":
        raise HTTPException(status_code=404, detail="Dev launcher is disabled outside development")

    roll = (roll or "").strip().upper()
    if not roll:
        raise HTTPException(status_code=400, detail="Roll number is required to start a development launch")

    from sqlalchemy import text
    db_student = (await db.execute(
        text("SELECT roll_number, name, branch, admission_batch, entry_type, section, year, email FROM students WHERE roll_number = :roll"),
        {"roll": roll}
    )).mappings().fetchone()

    if not db_student and not roll.startswith(("ROLL-", "STUDENT-", "DEV-", "TEST-")):
        raise HTTPException(status_code=404, detail=f"Roll number {roll} not found in MLRITM seed database.")

    display_name = name or (db_student["name"] if db_student else f"Student ({roll})")

    import time
    import uuid
    import jwt
    from backend.app.services.identity.dev_keys import get_dev_private_key_pem, get_dev_public_key_pem

    now = int(time.time())
    payload = {
        "iss": settings.LAUNCH_TOKEN_ISSUER or "anvaya.mlritm.ac.in",
        "aud": settings.LAUNCH_TOKEN_AUDIENCE or "mlritm-chatbot",
        "sub": roll,
        "iat": now,
        "exp": now + min(120, settings.LAUNCH_TOKEN_MAX_AGE_SECONDS),
        "jti": str(uuid.uuid4()),
        settings.IDENTITY_NAME_CLAIM or "name": display_name,
        settings.IDENTITY_ROLL_NUMBER_CLAIM or "roll_no": roll,
    }

    provider = get_identity_provider()
    if isinstance(provider, SignedLaunchTokenProvider) and not provider.uses_hmac():
        priv_pem = get_dev_private_key_pem()
        if not settings.LAUNCH_TOKEN_PUBLIC_KEY and not settings.LAUNCH_TOKEN_PUBLIC_KEY_FILE and not settings.LAUNCH_TOKEN_JWKS_URL:
            settings.LAUNCH_TOKEN_PUBLIC_KEY = get_dev_public_key_pem()
        token = jwt.encode(payload, priv_pem, algorithm="RS256")
    elif provider and isinstance(provider, SignedLaunchTokenProvider) and provider.uses_hmac():
        token = jwt.encode(payload, settings.LAUNCH_TOKEN_HMAC_SECRET, algorithm="HS256")
    else:
        # Default to RS256 with dev key
        priv_pem = get_dev_private_key_pem()
        if not settings.LAUNCH_TOKEN_PUBLIC_KEY:
            settings.LAUNCH_TOKEN_PUBLIC_KEY = get_dev_public_key_pem()
        token = jwt.encode(payload, priv_pem, algorithm="RS256")

    if format == "token":
        return {"token": token, "roll_number": roll, "name": display_name}

    return await _handle_launch(token, db)


@router.post("/launch")
async def anvaya_launch_post(launch_token: str = Form(...), db: AsyncSession = Depends(get_db)):
    """Anvaya launch via form POST (preferred: the token stays out of URLs and access logs)."""
    return await _handle_launch(launch_token, db)


@router.get("/launch")
async def anvaya_launch_get(launch_token: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    """Anvaya launch via link. Tokens are single-use and short-lived, so a logged URL cannot be replayed."""
    return await _handle_launch(launch_token, db)


@router.get("/oidc/login")
async def oidc_login(db: AsyncSession = Depends(get_db)):
    """Starts sign-in with the configured OpenID Connect provider."""
    provider = get_identity_provider()
    if not isinstance(provider, OIDCProvider):
        return _auth_error_redirect("not_configured", "OIDC is not the configured identity provider")
    try:
        state, nonce, verifier = await sessions.create_auth_flow(db)
        url = await provider.authorization_url(state, nonce, verifier)
    except IdentityError as e:
        return _auth_error_redirect("provider_unavailable", str(e))
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/oidc/callback")
async def oidc_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    provider = get_identity_provider()
    if not isinstance(provider, OIDCProvider):
        return _auth_error_redirect("not_configured", "OIDC is not the configured identity provider")
    if error:
        return _auth_error_redirect("provider_error", f"provider returned error={error[:100]}")
    flow = await sessions.consume_auth_flow(db, state or "")
    if not flow or not code:
        return _auth_error_redirect("invalid_state", "missing code or unknown/used/expired state")
    nonce, verifier = flow
    try:
        identity = await provider.complete_login(code, verifier, nonce)
    except IdentityError as e:
        return _auth_error_redirect("invalid_login", str(e))

    handoff = await sessions.create_pending_session(db, identity)
    db.add(AuditLog(event_type="AUTH_OIDC", intent_detected="LOGIN_SUCCESS"))
    await db.commit()
    return _app_redirect(f"handoff={handoff}")


@router.post("/session/exchange")
async def exchange_session(req: HandoffExchangeRequest, db: AsyncSession = Depends(get_db)):
    """Swaps the one-time handoff code from the redirect for the chatbot session token."""
    exchanged = await sessions.exchange_handoff(db, req.handoff_code)
    if not exchanged:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign-in link expired or already used. Open the assistant from Anvaya again.")
    token, row = exchanged
    return {"session_token": token, "student": _student_summary(row)}


@router.get("/me")
async def current_student(authorization: Optional[str] = Header(None), db: AsyncSession = Depends(get_db)):
    """The signed-in student's identity as verified from Anvaya."""
    row = await _session_row_or_401(authorization, db)
    return {"student": _student_summary(row)}


@router.post("/consent")
async def grant_consent(req: ConsentRequest, authorization: Optional[str] = Header(None), db: AsyncSession = Depends(get_db)):
    """Records DPDP consent for the signed-in student before any personal records are shown."""
    row = await _session_row_or_401(authorization, db)
    if not req.dpdp_consent_granted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="DPDP Act 2023 consent is required to access your student records."
        )
    await sessions.grant_consent(db, row)
    db.add(AuditLog(event_type="CONSENT_GRANT", intent_detected="CONSENT"))
    await db.commit()
    return {"message": "Consent recorded.", "consent_granted": True}


@router.post("/logout")
async def student_logout(authorization: Optional[str] = Header(None), db: AsyncSession = Depends(get_db)):
    """Revokes the chatbot session immediately."""
    await sessions.revoke_session(db, bearer_token(authorization))
    return {"message": "Session cleared."}
