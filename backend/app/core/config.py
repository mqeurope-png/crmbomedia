from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CRMBO Media CRM"
    environment: str = "development"
    database_url: str = "sqlite+pysqlite:///:memory:"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:3000"
    secret_key: str = Field(default="change-me-before-production", min_length=16)
    access_token_expire_minutes: int = 480
    default_admin_email: str = "admin@example.com"
    default_admin_password: str = "change-me-admin-password"
    integration_secrets_key: str = Field(
        ...,
        description=(
            "Fernet key (44 chars, urlsafe base64) used to encrypt integration "
            "API keys at rest. Generate with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ),
    )

    # Public URL the user clicks in the password-reset email. Used to build
    # the reset link; never sent back to the client by the API.
    frontend_base_url: str = "http://localhost:3000"

    # SMTP configuration. All fields are optional so the app keeps booting
    # without an email service; the factory in app/services/email.py picks
    # SMTPEmailService only when ENVIRONMENT=production AND smtp_host is set,
    # and falls back to ConsoleEmailService (with a warning in production)
    # otherwise. See docs/security.md "Email service".
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_from_name: str = "CRMBO Media CRM"
    smtp_use_tls: bool = True   # STARTTLS on port 587
    smtp_use_ssl: bool = False  # implicit SSL on port 465; mutually exclusive with use_tls

    # GDPR / RGPD subject-rights workflow. `access` and `portability`
    # requests write JSON/CSV exports to disk so an operator can hand them
    # to the data subject through a separate (signed) channel. The path is
    # relative-friendly; the service creates it on first use.
    gdpr_export_root: str = "var/gdpr_exports"

    # Error tracking. Sentry is initialized only when sentry_dsn is set, so
    # development and CI stay completely offline. release defaults to the
    # short git SHA in CI (export GIT_SHA=$GITHUB_SHA in the workflow).
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    git_sha: str | None = None

    # Anthropic Claude API key for AI-assisted pipeline generation.
    # Opt-in: when unset the "Generar con IA" surface stays hidden on
    # the frontend and the endpoint 503s. The key NEVER leaves the
    # backend — the frontend only reads the computed
    # `ai_features_enabled` flag via `GET /api/health`.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @field_validator("integration_secrets_key")
    @classmethod
    def validate_fernet_key(cls, value: str) -> str:
        from cryptography.fernet import Fernet

        try:
            Fernet(value.encode())
        except Exception as exc:
            raise ValueError(
                "INTEGRATION_SECRETS_KEY must be a valid Fernet key (44-char "
                "urlsafe base64). Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def ai_features_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
