"""
Application Configuration and Settings.
Loads environment variables using Pydantic Settings with secure defaults.
"""

import os
from typing import List
try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:
    SettingsConfigDict = None
    try:
        from pydantic import BaseSettings
    except ImportError:
        from pydantic import BaseModel as BaseSettings

import configparser


def _load_anvaya_config_file():
    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "anvaya.config"))
    if os.path.exists(cfg_path):
        parser = configparser.ConfigParser()
        parser.read(cfg_path, encoding="utf-8")
        if "anvaya_api" in parser:
            sec = parser["anvaya_api"]
            mapping = {
                "base_url": "ANVAYA_API_BASE_URL",
                "sandbox_url": "ANVAYA_API_SANDBOX_URL",
                "use_sandbox": "ANVAYA_API_USE_SANDBOX",
                "auth_method": "ANVAYA_API_AUTH",
                "api_key_header": "ANVAYA_API_KEY_HEADER",
                "oauth_token_url": "ANVAYA_OAUTH_TOKEN_URL",
                "oauth_client_id": "ANVAYA_OAUTH_CLIENT_ID",
                "timeout_seconds": "ANVAYA_API_TIMEOUT_SECONDS"
            }
            for k, env_k in mapping.items():
                v = sec.get(k, "").strip()
                if env_k not in os.environ and v:
                    os.environ[env_k] = v
        if "endpoints" in parser:
            for k, v in parser["endpoints"].items():
                env_k = f"ANVAYA_PATH_{k.upper()}"
                v_clean = v.strip()
                if env_k not in os.environ and v_clean:
                    os.environ[env_k] = v_clean
        if "deep_links" in parser:
            for k, v in parser["deep_links"].items():
                env_k = f"ANVAYA_LINK_{k.upper()}"
                v_clean = v.strip()
                if env_k not in os.environ and v_clean:
                    os.environ[env_k] = v_clean


_load_anvaya_config_file()


class Settings(BaseSettings):
    PROJECT_NAME: str = "MLRITM Academic Advising Chatbot"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "mlritm-secret-key-change-in-production-2024")
    AES_ENCRYPTION_KEY: str = os.getenv("AES_ENCRYPTION_KEY", "0123456789abcdef0123456789abcdef")  # 32-byte hex for AES-256
    
    # LLM Settings
    MODEL_PATH: str = os.getenv("MODEL_PATH", "models/mlritm-llama2-7b-q4_k_m.gguf")
    HF_TOKEN: str = os.getenv("HF_TOKEN", "")
    GBNF_GRAMMAR_PATH: str = "backend/app/llm/grammars/intent_schema.gbnf"

    # "development" or "production". Sample data and the dev launch simulator only work in development.
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    ANVAYA_BASE_URL: str = os.getenv("ANVAYA_BASE_URL", "https://anvaya.mlritm.ac.in")
    ANVAYA_LOGIN_URL: str = os.getenv("ANVAYA_LOGIN_URL", "https://anvaya.mlritm.ac.in/Login")
    ANVAYA_APP_URL: str = os.getenv("ANVAYA_APP_URL", "https://anvaya.mlritm.ac.in/App")

    # ---------------------------------------------------------------------
    # Anvaya identity integration. Everything is OFF until the college / ORGMAKER supplies
    # an official mechanism and its credentials. Values come from their documentation.
    #   "none"          - no student sign-in; personal records are unavailable
    #   "signed_launch" - Anvaya opens the assistant with a signed JWT launch token
    #   "oidc"          - Anvaya acts as an OpenID Connect provider (authorization code + PKCE)
    # ---------------------------------------------------------------------
    IDENTITY_PROVIDER: str = os.getenv("IDENTITY_PROVIDER", "none")

    # Claim mapping (server-side). STUDENT_KEY_CLAIM identifies the student's records in the
    # official data source; the browser can never supply or change it.
    IDENTITY_STUDENT_KEY_CLAIM: str = os.getenv("IDENTITY_STUDENT_KEY_CLAIM", "sub")
    IDENTITY_ROLL_NUMBER_CLAIM: str = os.getenv("IDENTITY_ROLL_NUMBER_CLAIM", "")
    IDENTITY_NAME_CLAIM: str = os.getenv("IDENTITY_NAME_CLAIM", "name")
    # Optional: only admit identities whose ROLE_CLAIM has one of these values (comma-separated)
    IDENTITY_ROLE_CLAIM: str = os.getenv("IDENTITY_ROLE_CLAIM", "")
    IDENTITY_STUDENT_ROLE_VALUES: str = os.getenv("IDENTITY_STUDENT_ROLE_VALUES", "")

    # Signed launch token (JWT) issued by Anvaya
    LAUNCH_TOKEN_ISSUER: str = os.getenv("LAUNCH_TOKEN_ISSUER", "")
    LAUNCH_TOKEN_AUDIENCE: str = os.getenv("LAUNCH_TOKEN_AUDIENCE", "")
    LAUNCH_TOKEN_ALGORITHMS: str = os.getenv("LAUNCH_TOKEN_ALGORITHMS", "RS256")
    LAUNCH_TOKEN_PUBLIC_KEY: str = os.getenv("LAUNCH_TOKEN_PUBLIC_KEY", "")        # PEM
    LAUNCH_TOKEN_PUBLIC_KEY_FILE: str = os.getenv("LAUNCH_TOKEN_PUBLIC_KEY_FILE", "")
    LAUNCH_TOKEN_JWKS_URL: str = os.getenv("LAUNCH_TOKEN_JWKS_URL", "")
    LAUNCH_TOKEN_HMAC_SECRET: str = os.getenv("LAUNCH_TOKEN_HMAC_SECRET", "")      # only for HS* algorithms
    LAUNCH_TOKEN_MAX_AGE_SECONDS: int = int(os.getenv("LAUNCH_TOKEN_MAX_AGE_SECONDS", "300"))

    # OpenID Connect
    OIDC_ISSUER: str = os.getenv("OIDC_ISSUER", "")
    OIDC_CLIENT_ID: str = os.getenv("OIDC_CLIENT_ID", "")
    OIDC_CLIENT_SECRET: str = os.getenv("OIDC_CLIENT_SECRET", "")
    OIDC_REDIRECT_URI: str = os.getenv("OIDC_REDIRECT_URI", "")
    OIDC_SCOPES: str = os.getenv("OIDC_SCOPES", "openid profile")

    # ---------------------------------------------------------------------
    # Student data source (the Student Data Service)
    #   "none"       - personal records unavailable (default, fail-closed)
    #   "anvaya_api" - official server-to-server interface, configured below
    #   "sample"     - labelled development records; refused outside development
    # ---------------------------------------------------------------------
    STUDENT_DATA_PROVIDER: str = os.getenv("STUDENT_DATA_PROVIDER", "none")

    # ---------------------------------------------------------------------
    # Anvaya Live Data Integration (Dual-Mode: API vs Deep-Link Fallback)
    # ---------------------------------------------------------------------
    ANVAYA_API_BASE_URL: str = os.getenv("ANVAYA_API_BASE_URL", "")
    ANVAYA_API_SANDBOX_URL: str = os.getenv("ANVAYA_API_SANDBOX_URL", "https://sandbox.anvaya.mlritm.ac.in/api")
    ANVAYA_API_USE_SANDBOX: bool = os.getenv("ANVAYA_API_USE_SANDBOX", "false").strip().lower() in ("1", "true", "yes", "on")
    
    # Auth scheme: "none" | "api_key" | "oauth2_client_credentials"
    ANVAYA_API_AUTH: str = os.getenv("ANVAYA_API_AUTH", "none")
    ANVAYA_API_KEY: str = os.getenv("ANVAYA_API_KEY", "")
    ANVAYA_API_KEY_HEADER: str = os.getenv("ANVAYA_API_KEY_HEADER", "X-API-Key")

    ANVAYA_OAUTH_TOKEN_URL: str = os.getenv("ANVAYA_OAUTH_TOKEN_URL", "")
    ANVAYA_OAUTH_CLIENT_ID: str = os.getenv("ANVAYA_OAUTH_CLIENT_ID", "")
    ANVAYA_OAUTH_CLIENT_SECRET: str = os.getenv("ANVAYA_OAUTH_CLIENT_SECRET", "")
    
    ANVAYA_API_STUDENT_PATH: str = os.getenv("ANVAYA_API_STUDENT_PATH", "")
    ANVAYA_API_AUTH_SCHEME: str = os.getenv("ANVAYA_API_AUTH_SCHEME", "bearer")
    ANVAYA_API_TOKEN: str = os.getenv("ANVAYA_API_TOKEN", "")
    ANVAYA_API_TOKEN_HEADER: str = os.getenv("ANVAYA_API_TOKEN_HEADER", "X-API-Key")
    ANVAYA_API_TIMEOUT_SECONDS: float = float(os.getenv("ANVAYA_API_TIMEOUT_SECONDS", "10"))

    # Per-feature live read endpoints (empty = use deep-link fallback)
    ANVAYA_PATH_ATTENDANCE: str = os.getenv("ANVAYA_PATH_ATTENDANCE", "")
    ANVAYA_PATH_MARKS: str = os.getenv("ANVAYA_PATH_MARKS", "")
    ANVAYA_PATH_RESULTS: str = os.getenv("ANVAYA_PATH_RESULTS", "")
    ANVAYA_PATH_FEES: str = os.getenv("ANVAYA_PATH_FEES", "")
    ANVAYA_PATH_TIMETABLE: str = os.getenv("ANVAYA_PATH_TIMETABLE", "")
    ANVAYA_PATH_EXAMS: str = os.getenv("ANVAYA_PATH_EXAMS", "")
    ANVAYA_PATH_PROFILE: str = os.getenv("ANVAYA_PATH_PROFILE", "")

    # Per-feature Anvaya Deep-Link Fallback URLs
    ANVAYA_LINK_ATTENDANCE: str = os.getenv("ANVAYA_LINK_ATTENDANCE", "https://anvaya.mlritm.ac.in/App/StudentAttendance")
    ANVAYA_LINK_MARKS: str = os.getenv("ANVAYA_LINK_MARKS", "https://anvaya.mlritm.ac.in/App/InternalMarks")
    ANVAYA_LINK_RESULTS: str = os.getenv("ANVAYA_LINK_RESULTS", "https://anvaya.mlritm.ac.in/App/ExamResults")
    ANVAYA_LINK_FEES: str = os.getenv("ANVAYA_LINK_FEES", "https://anvaya.mlritm.ac.in/App/FeePayments")
    ANVAYA_LINK_TIMETABLE: str = os.getenv("ANVAYA_LINK_TIMETABLE", "https://anvaya.mlritm.ac.in/App/ClassTimetable")
    ANVAYA_LINK_EXAMS: str = os.getenv("ANVAYA_LINK_EXAMS", "https://anvaya.mlritm.ac.in/App/ExamSchedule")
    ANVAYA_LINK_PROFILE: str = os.getenv("ANVAYA_LINK_PROFILE", "https://anvaya.mlritm.ac.in/App/StudentProfile")

    # In-memory ephemeral live cache TTL (DPDP Act 2023 compliant)
    LIVE_CACHE_TTL_SECONDS: int = int(os.getenv("LIVE_CACHE_TTL_SECONDS", "180"))

    # Profile synchronization
    PROFILE_SYNC_TTL_SECONDS: int = int(os.getenv("PROFILE_SYNC_TTL_SECONDS", "900"))
    PROFILE_MAX_STALE_SECONDS: int = int(os.getenv("PROFILE_MAX_STALE_SECONDS", "86400"))

    # Database Settings
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./academic_chatbot.db")
    
    # Security & CORS — read from env var (JSON array) so Render/Netlify URLs are included
    ALLOWED_ORIGINS: List[str] = []

    def __init__(self, **data):
        super().__init__(**data)
        import json as _json
        raw = os.getenv("ALLOWED_ORIGINS", "")
        if raw.strip():
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, list):
                    object.__setattr__(self, "ALLOWED_ORIGINS", parsed)
            except Exception:
                pass
        if not self.ALLOWED_ORIGINS:
            object.__setattr__(self, "ALLOWED_ORIGINS", [
                "https://aichatbotmlritm.netlify.app",
                "https://anvaya.mlritm.ac.in",
                "http://localhost:3000",
                "http://localhost:8000",
                "http://127.0.0.1:8000",
            ])

    
    # Redirect plain-HTTP requests to HTTPS (health probes excepted). Turn on wherever the assistant
    # is served to students; behind a reverse proxy the proxy must set X-Forwarded-Proto.
    ENFORCE_HTTPS: bool = os.getenv("ENFORCE_HTTPS", "false").strip().lower() in ("1", "true", "yes", "on")

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 30
    SESSION_TTL_MINUTES: int = 20

    @property
    def effective_anvaya_api_base(self) -> str:
        """Returns the active API base URL (sandbox or production), or empty string if disabled."""
        if self.ANVAYA_API_USE_SANDBOX:
            return self.ANVAYA_API_SANDBOX_URL.strip().rstrip("/")
        return self.ANVAYA_API_BASE_URL.strip().rstrip("/")

    @property
    def is_fallback_mode(self) -> bool:
        """True when no API base URL is configured; operates purely via deep-links."""
        return not bool(self.effective_anvaya_api_base)

    if SettingsConfigDict is not None:
        model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")
    else:  # pydantic v1 fallback
        class Config:
            env_file = ".env"
            case_sensitive = True
            extra = "ignore"


settings = Settings()

