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


@lru_cache
def get_settings() -> Settings:
    return Settings()
