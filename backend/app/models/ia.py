"""La IA en BoHub: cada llamada al modelo y cada borrador del asistente de
respuesta (migración 0137).

- `ia_llamadas`: una fila por llamada a Claude, sea del clasificador de leads
  o del asistente: propósito, modelo, tokens de entrada y salida, coste en
  USD (precio del modelo en el momento), quién la pidió y a qué se refiere.
  Es lo que enseña la pantalla de la IA: números reales, no estimaciones.
- `ia_borradores`: cada borrador que el asistente «Ayúdame a contestar» ha
  generado: para qué hilo y mensaje, para qué contacto, quién lo pidió, en
  qué idioma, con qué modelo y coste, el texto, los enlaces que se le
  ofrecieron, el resumen del contexto que entró y qué pasó con él (enviado
  tal cual, editado, descartado, regenerado). Nunca se envía solo: el envío
  lo hace una persona desde el compositor, y entonces se anota el resultado.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: Propósitos de las llamadas.
PROPOSITO_ASISTENTE = "asistente_borrador"
PROPOSITO_CLASIFICADOR = "clasificador_leads"

#: Estados de un borrador del asistente.
ESTADO_GENERADO = "generado"
ESTADO_REGENERADO = "regenerado"          # se pidió otro en su lugar
ESTADO_DESCARTADO = "descartado"
ESTADO_ENVIADO_TAL_CUAL = "enviado_tal_cual"
ESTADO_ENVIADO_EDITADO = "enviado_editado"
ESTADOS: tuple[str, ...] = (
    ESTADO_GENERADO, ESTADO_REGENERADO, ESTADO_DESCARTADO, ESTADO_ENVIADO_TAL_CUAL,
    ESTADO_ENVIADO_EDITADO,
)

#: Alcances del histórico que mira el asistente (C bis).
ALCANCE_HILO = "hilo"
ALCANCE_CONTACTO = "contacto"
ALCANCE_EMPRESA = "empresa"
ALCANCES: tuple[str, ...] = (ALCANCE_HILO, ALCANCE_CONTACTO, ALCANCE_EMPRESA)


def _uuid() -> str:
    return str(uuid4())


class LlamadaIA(TimestampMixin, Base):
    __tablename__ = "ia_llamadas"
    __table_args__ = (
        Index("ix_ia_llamadas_proposito_fecha", "proposito", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    proposito: Mapped[str] = mapped_column(String(40), nullable=False)
    modelo: Mapped[str] = mapped_column(String(80), nullable=False)
    tokens_entrada: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coste_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duracion_ms: Mapped[int | None] = mapped_column(Integer)
    usuario_id: Mapped[str | None] = mapped_column(String(36), index=True)
    referencia_tipo: Mapped[str | None] = mapped_column(String(30))
    referencia_id: Mapped[str | None] = mapped_column(String(64))
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(String(255))


class BorradorAsistente(TimestampMixin, Base):
    __tablename__ = "ia_borradores"
    __table_args__ = (
        Index("ix_ia_borradores_thread", "thread_id", "created_at"),
        Index("ix_ia_borradores_usuario", "usuario_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: El mensaje al que responde (el último del cliente).
    message_id: Mapped[str | None] = mapped_column(String(36))
    contact_id: Mapped[str | None] = mapped_column(String(36), index=True)
    usuario_id: Mapped[str] = mapped_column(String(36), nullable=False)
    llamada_id: Mapped[str | None] = mapped_column(String(36))
    idioma: Mapped[str | None] = mapped_column(String(5))
    modelo: Mapped[str] = mapped_column(String(80), nullable=False)
    tokens_entrada: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coste_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    destinatario: Mapped[str | None] = mapped_column(String(255))
    remitente: Mapped[str | None] = mapped_column(String(255))
    asunto: Mapped[str | None] = mapped_column(String(500))
    #: El borrador en texto plano (párrafos separados por línea en blanco).
    texto: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Lo que el modelo dijo no poder responder con los datos (JSON de lista).
    pendiente_json: Mapped[str | None] = mapped_column(Text)
    #: Los enlaces que se le ofrecieron (JSON de lista de dicts).
    enlaces_json: Mapped[str | None] = mapped_column(Text)
    #: Resumen de lo que entró en el contexto (JSON): mensajes del hilo,
    #: ficha, pedidos, máquina, enlaces, y (C bis) niveles y correos viejos.
    contexto_json: Mapped[str | None] = mapped_column(Text)
    #: Alcance del histórico elegido por quien pulsó (hilo / contacto / empresa).
    alcance: Mapped[str] = mapped_column(String(10), nullable=False, default=ALCANCE_CONTACTO)
    mensajes_hilo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mensajes_historico: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default=ESTADO_GENERADO)
    #: El mensaje que acabó enviándose a partir de este borrador.
    email_message_id: Mapped[str | None] = mapped_column(String(36))
    resuelto_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
