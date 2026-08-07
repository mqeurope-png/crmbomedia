from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "BoHub CRM"
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
    smtp_from_name: str = "BoHub CRM"
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

    # Brevo webhook signature secret. Optional but recommended: when
    # set, POST /api/webhooks/brevo rejects deliveries whose token
    # header doesn't match; when unset, deliveries are accepted with a
    # security warning in the logs. Configure the same value in Brevo
    # (Settings → Webhooks → signature/auth header).
    brevo_webhook_secret: str | None = None

    # Google Calendar OAuth. When any of these is unset the integration
    # endpoints respond 503 instead of 500 — the UI surfaces a clear
    # "not configured by admin" message. See
    # docs/integrations-google-calendar.md for the Cloud Console setup.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_redirect_uri: str | None = None
    # Timezone used when serialising task due_at to Google Calendar
    # events. Single-tenant for now — multi-timezone is a future PR.
    google_calendar_timezone: str = "Europe/Madrid"
    # Default event duration when a task has no end time. 30 minutes
    # matches the spec.
    google_calendar_default_event_minutes: int = 30

    # Sprint Email v1 — Gmail Push Notifications via Cloud Pub/Sub.
    # When `gmail_pubsub_topic` is empty the webhook path 503s with
    # "not configured by admin" and watch registration is a no-op.
    gmail_pubsub_project_id: str | None = None
    gmail_pubsub_topic: str | None = None
    gmail_pubsub_subscription: str | None = None
    gmail_pubsub_verification_token: str | None = None
    # CRM-GMAIL — verificación fuerte del push de Pub/Sub. Cuando la
    # suscripción se crea con autenticación (service account + OIDC token),
    # Google firma cada push con un JWT cuya `aud` es la URL del webhook y
    # cuyo `email` es el service account. Configurando estos dos, el webhook
    # valida firma + audiencia + emisor + service account (más fuerte que el
    # `gmail_pubsub_verification_token` de secreto compartido).
    gmail_webhook_jwt_audience: str | None = None
    gmail_webhook_service_account_email: str | None = None

    # PR-OAuth-Permisos-Admin Item 9. Mientras la app OAuth no esté
    # verificada por Google, los refresh tokens caducan a 7 días y hay
    # que avisar a los users. Cuando se verifique oficialmente, poner
    # GMAIL_APP_VERIFIED=true → el cron de aviso de caducidad sale early
    # (tokens ilimitados, no hace falta avisar).
    gmail_app_verified: bool = False

    # Sprint Email v2.2b — Supabase backing composer.bomedia.net. When
    # unset, the "Composer" tab in the template picker shows a clear
    # "not configured" notice instead of breaking the picker. Both keys
    # are required together; either both or neither.
    supabase_composer_url: str | None = None
    supabase_composer_key: str | None = None

    # Sprint Email v2.2b — local disk path for images uploaded from the
    # Tiptap editor in the send-email modal. Files are content-addressed
    # (sha256) and partitioned by year/month so a single directory never
    # grows past a few hundred entries.
    #
    # `email_assets_public_base` is the host that recipients' inboxes
    # will resolve the `<img src="...">` against. When unset the
    # endpoint emits a path-only URL — fine for dev / tests but images
    # will never render once the email leaves the local machine.
    email_assets_dir: str = "var/email_assets"
    email_assets_public_base: str = ""
    email_assets_max_bytes: int = 5 * 1024 * 1024

    # Sprint Web-Forms — reCAPTCHA v3 invisible para los formularios web
    # públicos. Sin ambas claves el anti-spam de recaptcha se salta (queda
    # honeypot + rate limit); el `site_key` es público (va en el widget),
    # el `secret` solo se usa server-side en la verificación con Google.
    recaptcha_site_key: str | None = None
    recaptcha_secret: str | None = None
    # Score mínimo aceptado (v3 devuelve 0.0-1.0). Por debajo → spam.
    recaptcha_min_score: float = 0.5
    # Host público donde el backend sirve /public/forms/* y /forms/* (el
    # embed code apunta aquí). Vacío → cae a frontend_base_url.
    web_forms_embed_base_url: str = ""

    # BoHub ERP Fase A — almacenamiento de documentos (fotos SAT, PDFs). El
    # backend abstracto `DocumentStorage` usa HiDrive si hay credenciales;
    # si no, cae a disco local en `erp_uploads_dir` (dev + fallback).
    hidrive_webdav_url: str = ""
    hidrive_user: str = ""
    hidrive_password: str = ""
    erp_uploads_dir: str = "uploads/erp"

    # BoHub ERP Fase D — storage de ficheros de expedición (albaranes +
    # etiquetas). `STORAGE_BACKEND`=local (default) usa disco local del VPS en
    # `LOCAL_SHIPPING_STORAGE_DIR`; =hidrive usará HiDrive cuando tenga espacio
    # (stub hoy). Ver app/storage.
    storage_backend: str = "local"
    local_shipping_storage_dir: str = "/opt/crmbo/uploads/erp-shipping"
    # D-1-fix2: token compartido con el mu-plugin `bohub-albaran` de cada tienda
    # WP (mismo token en las 3). Vacío → el CRM genera el albarán con reportlab.
    # El valor real vive SOLO en `.env.production` (no se commitea).
    woocommerce_albaran_token: str = ""

    # BoHub ERP Fase C — FACTUSOL (API DELSOL). Password cifrada con la
    # Fernet key existente (INTEGRATION_SECRETS_KEY); se descifra on-demand
    # y se envía en base64 al login. Ejercicio = año fiscal de los documentos.
    factusol_base_url: str = "https://api.sdelsol.com"
    factusol_codigo_fabricante: str = ""
    factusol_codigo_cliente: str = ""
    factusol_base_datos_cliente: str = ""
    factusol_password_encrypted: str = ""
    factusol_default_ejercicio: str = "2026"
    # C-1-fix1: las rutas de datos de la API DELSOL NO están confirmadas
    # (apidoc.sdelsol.com es inaccesible desde CI/dev y `/registros/*` da 404
    # en prod). Se exponen como env para corregirlas SIN redeploy de código:
    # `scripts/factusol_discover_paths.py` las descubre desde el VPS.
    factusol_path_load_table: str = ""
    factusol_path_write_record: str = ""
    factusol_path_update_record: str = ""
    factusol_path_delete_records: str = ""

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

    @property
    def supabase_composer_configured(self) -> bool:
        return bool(self.supabase_composer_url and self.supabase_composer_key)

    @property
    def recaptcha_configured(self) -> bool:
        return bool(self.recaptcha_site_key and self.recaptcha_secret)

    @property
    def google_calendar_configured(self) -> bool:
        return bool(
            self.google_oauth_client_id
            and self.google_oauth_client_secret
            and self.google_oauth_redirect_uri
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
