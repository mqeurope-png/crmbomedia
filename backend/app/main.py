from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.bulk import router as bulk_router
from app.api.companies import assign_router as contacts_assign_router
from app.api.companies import router as companies_router
from app.api.contact_channels import router as contact_channels_router
from app.api.dashboard import router as dashboard_router
from app.api.email_drafts import router as email_drafts_router
from app.api.emails import router as emails_router
from app.api.emails_mailbox import router as emails_mailbox_router
from app.api.emails_scheduled import router as emails_scheduled_router
from app.api.google_integrations import router as google_router
from app.api.routes import router
from app.api.tasks import router as tasks_router
from app.core.config import get_settings
from app.core.observability import setup_sentry
from app.email_signatures.router import router as email_signatures_router
from app.email_templates.router import router as email_templates_router
from app.email_tracking.router import router as email_tracking_router
from app.integrations.gmail.webhook import router as gmail_webhook_router

# Sentry must be initialized BEFORE the FastAPI app is created so its
# integrations can hook the request lifecycle. setup_sentry() is a no-op
# unless SENTRY_DSN is set, so this is safe in dev / tests / CI.
setup_sentry()

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "API central para CRM propio e integraciones con AgileCRM, "
        "Brevo, Freshdesk y FactuSOL."
    ),
    # The reverse proxy in production only routes `/api/*` to the
    # backend, so the default `/docs`, `/redoc` and `/openapi.json`
    # paths get swallowed by the Next.js app (404). Move all three
    # under the `/api` prefix so the frontend "OpenAPI" button and
    # any external integrator land on the right host.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sprint Empresas — the companies router takes precedence over the
# legacy /companies handler that lives inside routes.py so the v2
# Pydantic shape (domain, source, address, …) ships instead of the
# original {id, name, tax_id, website, is_active} subset. The
# legacy `/api/companies/count` stays accessible because it's a
# different path, not shadowed by the prefix.
app.include_router(companies_router)
app.include_router(contacts_assign_router)
app.include_router(contact_channels_router)
app.include_router(router, prefix="/api")
# Tasks router carries its own `/api/tasks` prefix and lives in its
# own module — the routes.py monolith was already pushing 4k lines
# before the productivity layer started.
app.include_router(tasks_router)
app.include_router(google_router)
app.include_router(dashboard_router)
app.include_router(bulk_router)
app.include_router(emails_router)
app.include_router(emails_mailbox_router)
app.include_router(emails_scheduled_router)
app.include_router(email_drafts_router)
app.include_router(gmail_webhook_router)
app.include_router(email_templates_router)
app.include_router(email_signatures_router)
app.include_router(email_tracking_router)

# Sprint Email v2.2 — serve email-template assets (Tiptap inline
# uploads). In production nginx aliases `/assets/email-templates/`
# straight to the host bind mount, so this mount mostly exists for
# dev / tests where there's no reverse proxy.
_email_assets_dir = Path(settings.email_assets_dir)
_email_assets_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/assets/email-templates",
    StaticFiles(directory=str(_email_assets_dir)),
    name="email_assets",
)


@app.on_event("startup")
async def _arm_brevo_periodics() -> None:
    """Arm Brevo's self-rescheduling heartbeats at API startup. The
    SETNX guards in `arm_periodic_jobs` make this idempotent across
    multiple API processes and across restarts. Wrapped in a try so a
    Redis outage at boot doesn't take the API down — the next click
    on `Sincronizar ahora` will trigger one-shot enqueues anyway."""
    try:
        # Side-effect import wires the OPERATIONS registry; calling
        # it here keeps the scheduler responsible for its own armor.
        from app.integrations.brevo.scheduler import (  # noqa: PLC0415
            arm_periodic_jobs,
        )

        arm_periodic_jobs()
    except Exception:  # noqa: BLE001
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).warning(
            "brevo.scheduler arm failed at startup", exc_info=True
        )


@app.on_event("startup")
async def _arm_email_scheduled_sweep() -> None:
    """Sprint Email v2.4e — arm the periodic scheduled-send sweep
    at API startup. SETNX-guarded so multiple API processes coexist;
    the job re-schedules itself on every tick. The RQ queue name
    stays `emails:snooze_sweep` (v2.4c heritage) so the prod worker
    container doesn't need a config change."""
    try:
        from app.email_scheduled_sweep import (  # noqa: PLC0415
            arm_scheduled_sweep,
        )

        arm_scheduled_sweep()
    except Exception:  # noqa: BLE001
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).warning(
            "email.scheduled_send_sweep arm failed at startup",
            exc_info=True,
        )
