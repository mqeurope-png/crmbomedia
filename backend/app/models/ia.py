"""La IA en BoHub: cada llamada al modelo y cada borrador del asistente de
respuesta (migración 0137).

- `ia_llamadas`: una fila por llamada a Claude, sea del clasificador de leads
  o del asistente: propósito, modelo, tokens de entrada y salida, coste en
  USD (precio del modelo en el momento), quién la pidió y a qué se refiere.
  Es lo que enseña la pantalla de la IA: números reales, no estimaciones.
- `ia_borradores`: cada borrador que el asistente «Ayúdame a contestar» ha
  generado: para qué hilo y mensaje (o ninguno: un correo nuevo), para qué
  contacto, quién lo pidió, en qué idioma, con qué modelo y coste, el texto,
  los enlaces que se le ofrecieron, el resumen del contexto que entró y qué
  pasó con él (enviado tal cual, editado, descartado, regenerado). Desde la
  migración 0138 es una CONVERSACIÓN: el primer borrador (turno 0) guarda el
  bloque de contexto ya montado (`contexto_texto`) y cada corrección («más
  corto», «en inglés») es un turno más que lo reutiliza sin volver a montarlo
  ni a pagarlo entero (caché de prompts). `instruccion` es lo que pidió la
  persona; `aviso` lo que la IA tiene que decirle a ELLA (nunca va en el
  cuerpo). Nunca se envía solo: el envío lo hace una persona desde el
  compositor, y entonces se anota el resultado.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: Propósitos de las llamadas. El primer borrador y las correcciones se
#: anotan aparte: lo que cuesta un borrador terminado es el primero más sus
#: retoques, y se quiere ver cada parte.
PROPOSITO_ASISTENTE = "asistente_borrador"
PROPOSITO_CORRECCION = "asistente_correccion"
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
#: Sin hilo (un correo nuevo) «solo este hilo» no significa nada.
ALCANCES_SIN_HILO: tuple[str, ...] = (ALCANCE_CONTACTO, ALCANCE_EMPRESA)


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
    #: Caché de prompts: lo leído de caché cuesta una décima parte y lo escrito
    #: un cuarto más. `tokens_entrada` es solo lo que fue a precio entero.
    tokens_cache_lectura: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_cache_escritura: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
    #: El hilo al que responde; None si es un correo nuevo, sin hilo (0138).
    thread_id: Mapped[str | None] = mapped_column(String(36))
    #: El mensaje al que responde (el último del cliente).
    message_id: Mapped[str | None] = mapped_column(String(36))
    contact_id: Mapped[str | None] = mapped_column(String(36), index=True)
    usuario_id: Mapped[str] = mapped_column(String(36), nullable=False)
    llamada_id: Mapped[str | None] = mapped_column(String(36))
    #: La conversación: el id del primer borrador (turno 0) de la serie. Cada
    #: corrección es un turno más con `padre_id` = el borrador que corrige.
    conversacion_id: Mapped[str | None] = mapped_column(String(36), index=True)
    turno: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    padre_id: Mapped[str | None] = mapped_column(String(36))
    #: Lo que la persona escribió en la caja («pregúntale si recibió el
    #: presupuesto», «más corto»); None si dejó que la IA dedujera del hilo.
    instruccion: Mapped[str | None] = mapped_column(Text)
    #: Lo que la IA le dice a la PERSONA (no hay nada que contestar, falta un
    #: dato…). Va en la franja del asistente, nunca en el cuerpo del correo.
    aviso: Mapped[str | None] = mapped_column(Text)
    #: El bloque de contexto ya montado (hilo, expediente, enlaces), solo en el
    #: turno 0: las correcciones lo reutilizan tal cual (y la API lo sirve de
    #: caché), sin volver a montarlo ni a pagarlo entero.
    contexto_texto: Mapped[str | None] = mapped_column(Text)
    idioma: Mapped[str | None] = mapped_column(String(5))
    modelo: Mapped[str] = mapped_column(String(80), nullable=False)
    tokens_entrada: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_cache_lectura: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_cache_escritura: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coste_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    destinatario: Mapped[str | None] = mapped_column(String(255))
    remitente: Mapped[str | None] = mapped_column(String(255))
    asunto: Mapped[str | None] = mapped_column(String(500))
    #: El borrador en texto plano (párrafos separados por línea en blanco): lo
    #: que mira la guardia del cuerpo y con lo que se compara lo enviado.
    texto: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: El borrador en HTML sencillo (las etiquetas que admite el editor:
    #: listas, negritas, enlaces, encabezados, tabla simple), desde 0141. None
    #: en los anteriores: entonces el HTML se deriva de `texto`. LONGTEXT en
    #: MySQL, como los cuerpos de los correos.
    html: Mapped[str | None] = mapped_column(Text().with_variant(mysql.LONGTEXT(), "mysql"))
    #: `normal` (el cuerpo es texto para el editor) o `compuesto` (el turno es la
    #: estructura de un correo compuesto y `html` el correo generado), desde 0144.
    modo: Mapped[str] = mapped_column(String(12), nullable=False, default="normal",
                                      server_default="normal")
    #: La estructura del correo compuesto (JSON de bloques), desde 0144.
    estructura_json: Mapped[str | None] = mapped_column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"))
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
