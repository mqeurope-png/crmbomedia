"""Respuesta a leads · Fase 1 — la clasificación de cada lead.

Una fila por lead clasificado: lo que entró (la consulta y su contexto), lo
que salió (idioma, interés, spam, confianza y el motivo), de dónde salió cada
dato (el formulario, las etiquetas, la IA o las palabras clave), lo que se
preparó (borrador y tarea) y la corrección a mano si la hubo. Es el material
con el que se mide si la clasificación acierta antes de encender el envío
automático (Fase 2).

El contacto lleva además una copia de lo esencial (`lead_interest`,
`lead_is_spam`, `lead_confidence`, `lead_classified_at`) para que las
condiciones de los workflows y los filtros bifurquen por ello sin JOIN.

`(source, source_ref)` es única: un lead (un envío de formulario, una nota de
Agile) se clasifica UNA vez. Un segundo paso sobre el mismo lead reutiliza la
fila, no la duplica.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: De dónde viene el lead.
FUENTE_FORMULARIO = "web_form"
FUENTE_AGILE = "agilecrm"
FUENTE_MANUAL = "manual"
FUENTES: tuple[str, ...] = (FUENTE_FORMULARIO, FUENTE_AGILE, FUENTE_MANUAL)

#: Estado de la fila: qué se hizo con el lead.
ESTADO_CLASIFICADO = "clasificado"      # clasificado, todavía sin borrador
ESTADO_SPAM = "spam"                     # a «Descartado / spam», sin borrador ni tarea
ESTADO_PREPARADO = "preparado"           # borrador con plantilla preparado
ESTADO_SIN_PLANTILLA = "sin_plantilla"   # borrador vacío: sin plantilla para interés/idioma
ESTADO_OMITIDO = "omitido"               # no se procesó (antigüedad, tope, interruptor)
ESTADOS: tuple[str, ...] = (
    ESTADO_CLASIFICADO, ESTADO_SPAM, ESTADO_PREPARADO, ESTADO_SIN_PLANTILLA, ESTADO_OMITIDO,
)


class LeadClassification(TimestampMixin, Base):
    __tablename__ = "lead_classifications"
    __table_args__ = (
        UniqueConstraint("source", "source_ref", name="uq_lead_classifications_source_ref"),
        Index("ix_lead_classifications_contact", "contact_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Id del envío (`form_submissions`) o de la nota (`notes`).
    source_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    #: La fecha REAL del lead (la del envío o `notes.external_created_at`), no
    #: la de sincronización.
    lead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Lo que entró.
    input_text: Mapped[str | None] = mapped_column(Text)
    #: Contexto: web, formulario, idioma del formulario, productos marcados,
    #: país, cuenta de Agile, dominio del email.
    input_json: Mapped[str | None] = mapped_column(Text)

    # Lo que salió.
    language: Mapped[str | None] = mapped_column(String(5))
    #: formulario | texto | ia | palabras_clave | desconocido
    language_source: Mapped[str | None] = mapped_column(String(16))
    #: El formulario decía un idioma y el texto, claramente, otro.
    language_mismatch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    form_language: Mapped[str | None] = mapped_column(String(5))
    interest: Mapped[str | None] = mapped_column(String(40))
    #: etiquetas | ia | palabras_clave
    interest_source: Mapped[str | None] = mapped_column(String(16))
    is_spam: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(500))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80))

    # Lo que se hizo.
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ESTADO_CLASIFICADO
    )
    status_detail: Mapped[str | None] = mapped_column(String(200))
    template_id: Mapped[str | None] = mapped_column(String(36))
    template_name: Mapped[str | None] = mapped_column(String(200))
    sender_email: Mapped[str | None] = mapped_column(String(320))
    draft_id: Mapped[str | None] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="SET NULL")
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL")
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL")
    )

    # La corrección a mano (Configuración ERP → Respuesta a leads).
    corrected_language: Mapped[str | None] = mapped_column(String(5))
    corrected_interest: Mapped[str | None] = mapped_column(String(40))
    corrected_is_spam: Mapped[bool | None] = mapped_column(Boolean)
    corrected_by_user_id: Mapped[str | None] = mapped_column(String(36))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correction_note: Mapped[str | None] = mapped_column(String(500))

    # --- valores efectivos (la corrección manda sobre la clasificación) ----

    @property
    def idioma_efectivo(self) -> str | None:
        return self.corrected_language or self.language

    @property
    def interes_efectivo(self) -> str | None:
        return self.corrected_interest or self.interest

    @property
    def es_spam_efectivo(self) -> bool:
        if self.corrected_is_spam is not None:
            return bool(self.corrected_is_spam)
        return bool(self.is_spam)

    @property
    def corregida(self) -> bool:
        return self.corrected_at is not None

    def contexto(self) -> dict[str, Any]:
        try:
            data = json.loads(self.input_json or "{}")
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
