from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
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
