"""CRM-ETIQUETAS-GMAIL-V2.3 — import de labels personalizadas de Gmail.

Dos pasos, ambos idempotentes:

1. **Import de labels**: `users.labels.list` → upsert en `email_labels`
   como etiquetas ORG (`user_id NULL`, `gmail_label_id` = id upstream).
   Solo entran las de `type == 'user'` (las personalizadas que Bart creó
   en Gmail). Las de sistema se saltan: INBOX/SPAM/SENT/TRASH ya tienen
   vista nativa en el CRM y las CATEGORY_* de las pestañas de Gmail no
   aportan nada como etiqueta.

2. **Mapeo retroactivo**: los mensajes ya importados guardan sus labelIds
   en el JSON `email_messages.gmail_labels`; se materializa el mapeo en
   `email_message_labels` para las labels ahora conocidas. Re-ejecutar
   con más labels importadas solo añade lo que falta.

Se invoca desde `python -m app.integrations.gmail_watch sync_labels
[--dry-run]`. El go-forward (mails nuevos + cambios de label en Gmail)
lo cubre `service.process_history`; la pata CRM→Gmail son los endpoints
de mensaje (`messages.modify`).
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import EmailLabel, EmailMessage, EmailMessageLabel

logger = logging.getLogger(__name__)


def is_custom_label_id(label_id: str) -> bool:
    """Heurística barata para distinguir labels personalizadas sin llamar a
    la API: Gmail genera ids 'Label_<n>' para las de usuario; las de sistema
    son MAYÚSCULAS (INBOX, SPAM, CATEGORY_SOCIAL, …)."""
    return bool(label_id) and not label_id.isupper()


@dataclass
class LabelSyncReport:
    dry_run: bool
    labels_found: int = 0  # labels type=user en Gmail
    labels_created: int = 0
    labels_updated: int = 0
    labels_skipped_system: int = 0
    messages_scanned: int = 0
    mappings_created: int = 0  # filas email_message_labels retroactivas
    duration_seconds: float = 0.0

    def render(self) -> str:
        bar = "━" * 41
        return "\n".join(
            [
                f"Sync de etiquetas {'(DRY-RUN) ' if self.dry_run else ''}"
                "completo.",
                bar,
                f"Labels personalizadas en Gmail: {self.labels_found:>6}",
                f"├── Importadas (nuevas):        {self.labels_created:>6}",
                f"├── Actualizadas (nombre/color):{self.labels_updated:>6}",
                f"└── De sistema ignoradas:       {self.labels_skipped_system:>6}",
                f"Mensajes con labels revisados:  {self.messages_scanned:>6}",
                f"Mapeos mensaje↔etiqueta nuevos: {self.mappings_created:>6}",
                f"Duración:                       {self.duration_seconds:>5.1f}s",
                bar,
            ]
        )


def org_label_map(session: Session) -> dict[str, EmailLabel]:
    """`{gmail_label_id: EmailLabel}` de las labels de Gmail ya importadas."""
    rows = session.scalars(
        select(EmailLabel).where(EmailLabel.gmail_label_id.is_not(None))
    )
    return {row.gmail_label_id: row for row in rows if row.gmail_label_id}


def upsert_gmail_label(
    session: Session, *, raw: dict
) -> EmailLabel | None:
    """Crea (o actualiza nombre/color de) la etiqueta org espejo de una
    label de Gmail. Devuelve None si la label no es de usuario."""
    if (raw or {}).get("type") != "user":
        return None
    gid = raw.get("id") or ""
    if not gid:
        return None
    name = raw.get("name") or gid
    color = (raw.get("color") or {}).get("backgroundColor")
    label = session.scalar(
        select(EmailLabel).where(EmailLabel.gmail_label_id == gid)
    )
    if label is None:
        label = EmailLabel(
            user_id=None,
            name=name,
            color=color,
            gmail_label_id=gid,
        )
        session.add(label)
        session.flush()
        return label
    if label.name != name:
        label.name = name
    if color and label.color != color:
        label.color = color
    return label


def sync_gmail_labels(
    session: Session,
    *,
    user_id: str,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> LabelSyncReport:
    """Importa las labels personalizadas y materializa el mapeo retroactivo
    mensaje↔etiqueta desde el JSON `gmail_labels`. Idempotente. El caller
    hace el commit (en dry-run no hay nada que commitear)."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    emit = progress or (lambda _msg: None)
    client = gmail_service._client_for(session, user_id)
    report = LabelSyncReport(dry_run=dry_run)
    started = time.monotonic()

    # -- Paso 1: import de labels -------------------------------------
    label_map = org_label_map(session)
    for item in client.labels_list():
        if (item.get("type") or "") != "user":
            report.labels_skipped_system += 1
            continue
        gid = item.get("id") or ""
        if not gid:
            continue
        report.labels_found += 1
        name = item.get("name") or gid
        color = (item.get("color") or {}).get("backgroundColor")
        existing = label_map.get(gid)
        if existing is None:
            report.labels_created += 1
            if not dry_run:
                label_map[gid] = upsert_gmail_label(session, raw=item)
            else:
                # dry-run: marcador para que el paso 2 cuente los mapeos
                # que ESTA label generaría sin escribir la fila.
                label_map[gid] = None  # type: ignore[assignment]
        else:
            if existing.name != name or (color and existing.color != color):
                report.labels_updated += 1
                if not dry_run:
                    upsert_gmail_label(session, raw=item)
    emit(
        f"  labels: {report.labels_created} nuevas / "
        f"{report.labels_updated} actualizadas / "
        f"{report.labels_skipped_system} de sistema ignoradas"
    )

    # -- Paso 2: mapeo retroactivo ------------------------------------
    if label_map:
        # Pares ya existentes, clave (message_id, gmail_label_id) para que
        # el dry-run pueda contar sin ids de fila.
        existing_pairs: set[tuple[str, str]] = set(
            (message_id, gid)
            for message_id, gid in session.execute(
                select(EmailMessageLabel.message_id, EmailLabel.gmail_label_id)
                .join(EmailLabel, EmailLabel.id == EmailMessageLabel.label_id)
                .where(EmailLabel.gmail_label_id.is_not(None))
            )
        )
        now = datetime.now(UTC)
        rows = session.execute(
            select(EmailMessage.id, EmailMessage.gmail_labels).where(
                EmailMessage.gmail_account_user_id == user_id,
                EmailMessage.gmail_labels.is_not(None),
            )
        )
        for message_id, labels_json in rows:
            report.messages_scanned += 1
            try:
                labels = json.loads(labels_json) or []
            except (TypeError, ValueError):
                continue
            for gid in labels:
                if gid not in label_map:
                    continue
                pair = (message_id, gid)
                if pair in existing_pairs:
                    continue
                existing_pairs.add(pair)
                report.mappings_created += 1
                if not dry_run:
                    label = label_map[gid]
                    session.add(
                        EmailMessageLabel(
                            message_id=message_id,
                            label_id=label.id,
                            applied_at=now,
                        )
                    )
            if report.messages_scanned % 500 == 0:
                emit(
                    f"  … {report.messages_scanned} mensajes revisados "
                    f"({report.mappings_created} mapeos nuevos)"
                )
        if not dry_run:
            session.flush()

    report.duration_seconds = time.monotonic() - started
    return report
