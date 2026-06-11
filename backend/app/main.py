from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dashboard import router as dashboard_router
from app.api.google_integrations import router as google_router
from app.api.routes import router
from app.api.tasks import router as tasks_router
from app.core.config import get_settings
from app.core.observability import setup_sentry

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

app.include_router(router, prefix="/api")
# Tasks router carries its own `/api/tasks` prefix and lives in its
# own module — the routes.py monolith was already pushing 4k lines
# before the productivity layer started.
app.include_router(tasks_router)
app.include_router(google_router)
app.include_router(dashboard_router)


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
