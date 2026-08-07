"""Gmail Push Notifications receiver.

Cloud Pub/Sub pushes a JSON payload with a base64 message body and
a signed JWT in the `Authorization` header. The receiver:

1. Validates the JWT signature, issuer (`accounts.google.com`) and
   audience (`GMAIL_PUBSUB_VERIFICATION_TOKEN` or the webhook URL).
2. Decodes the Pub/Sub body to get `{emailAddress, historyId}`.
3. Looks up the matching `user_google_integrations` row.
4. Enqueues an RQ job to process the history slice — the receiver
   itself must return <5 s so we don't block Google's push.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _validate_jwt(authorization: str | None) -> None:
    """Verify the JWT signature + claims from Cloud Pub/Sub.

    CRM-GMAIL — verificación fuerte. Pub/Sub firma cada push con el
    service-account que la suscripción tiene configurado (OIDC). Cuando
    `gmail_webhook_jwt_audience` / `gmail_webhook_service_account_email`
    están puestos, exigimos:
      - firma válida (google-auth) + emisor `accounts.google.com`,
      - `aud` == audiencia configurada (la URL del webhook),
      - `email` == service account configurado + `email_verified`.
    Compat: si además hay `gmail_pubsub_verification_token`, aceptamos ese
    Bearer estático. Si NO hay ninguna verificación configurada, aceptamos
    (log warning) — como el webhook de Brevo — para no romper el arranque
    antes de que admin haga el setup de Cloud.
    """
    settings = get_settings()
    audience = settings.gmail_webhook_jwt_audience
    sa_email = settings.gmail_webhook_service_account_email
    shared_token = settings.gmail_pubsub_verification_token

    if not audience and not sa_email and not shared_token:
        logger.warning(
            "gmail.webhook.jwt_skipped reason=unconfigured — subscription "
            "accepts unsigned pushes; set GMAIL_WEBHOOK_JWT_AUDIENCE + "
            "GMAIL_WEBHOOK_SERVICE_ACCOUNT_EMAIL to enforce verification"
        )
        return

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
        )

    # Shared-secret path (compat) — acepta directamente si casa.
    if shared_token and authorization == f"Bearer {shared_token}":
        return

    # Verificación OIDC completa (Pub/Sub authenticated push).
    try:
        from google.auth.transport import requests as g_requests  # noqa: PLC0415
        from google.oauth2 import id_token as id_token_lib  # noqa: PLC0415

        token = authorization.removeprefix("Bearer ").strip()
        claims = id_token_lib.verify_oauth2_token(
            token,
            g_requests.Request(),
            audience=audience or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("gmail.webhook.jwt_invalid", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid push notification token.",
        ) from exc

    # El JWT es válido y firmado por Google; ahora exigimos que quien firma
    # sea NUESTRO service account (evita tokens Google legítimos de terceros).
    if sa_email:
        token_email = claims.get("email")
        if token_email != sa_email or not claims.get("email_verified", False):
            logger.warning(
                "gmail.webhook.jwt_wrong_sa email=%s", token_email
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Push token not from the expected service account.",
            )


def _decode_pubsub_payload(body: dict[str, Any]) -> dict[str, Any]:
    message = body.get("message", {})
    data_b64 = message.get("data")
    if not data_b64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty Pub/Sub message.",
        )
    try:
        decoded = base64.b64decode(data_b64).decode()
        return json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed Pub/Sub data payload.",
        ) from exc


@router.post("/gmail")
async def gmail_webhook(
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, str | int]:
    """Receive a Gmail Push Notifications push.

    Returns 200 fast — the actual history processing happens in the
    worker so Google doesn't time out.
    """
    _validate_jwt(request.headers.get("authorization"))
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body is not valid JSON.",
        ) from exc
    payload = _decode_pubsub_payload(body)
    email_address = payload.get("emailAddress")
    history_id = int(payload.get("historyId", 0))
    if not email_address or not history_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing emailAddress / historyId in Pub/Sub payload.",
        )

    # PR-OAuth-Google-Unificado. Antes había 6 integraciones per-user
    # con el MISMO google_email → 6 jobs (6 copias del mismo email). Ahora
    # hay UNA integración org compartida → UN job, atribuido al user que
    # conectó (`connected_by_user_id`). Los threads/messages quedan bajo
    # ese gmail_account_user_id.
    from app.integrations.google_calendar.service import (  # noqa: PLC0415
        get_org_integration,
    )

    org = get_org_integration(session)
    if (
        org is None
        or org.status != "active"
        or org.google_email != email_address
        or not org.connected_by_user_id
    ):
        logger.info(
            "gmail.webhook.no_active_org address=%s", email_address
        )
        return {"status": "ignored"}

    from app.integrations.gmail.jobs import enqueue_process_history  # noqa: PLC0415

    enqueue_process_history(
        user_id=org.connected_by_user_id, new_history_id=history_id
    )
    logger.info(
        "gmail.webhook.enqueued address=%s org_user=%s",
        email_address, org.connected_by_user_id,
    )
    return {"status": "enqueued", "users": 1}
