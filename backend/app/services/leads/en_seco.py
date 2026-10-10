"""Modo en seco: qué habría hecho la Fase 1 con los leads de los últimos N
días, SIN escribir nada. Es lo que se usa para medir el acierto de la
clasificación antes de encender el interruptor (y, más adelante, la Fase 2).

Clasifica con el mismo proveedor que el paso del workflow, resuelve plantilla
y remitente con la misma configuración, y devuelve por lead lo que haría:
etapa (Nuevo lead / Descartado · spam), plantilla, remitente, tarea. No toca
`lead_classifications`, ni el contacto, ni los pipelines, ni los borradores.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.crm import Contact, Note
from app.models.leads import FUENTE_AGILE, FUENTE_FORMULARIO, LeadClassification
from app.models.web_forms import FormSubmission, WebForm
from app.services.leads import intereses, plantillas, registro, remitentes
from app.services.leads.clasificador import (
    Clasificador,
    EntradaLead,
    clasificar_lead,
    proveedor_por_defecto,
)
from app.services.leads.config import configuracion
from app.services.leads.eventos import es_nota_de_formulario, texto_de_nota
from app.services.web_forms.sitios import clave_de_sitio, web_de_sitio

#: Leads por simulación. Cada uno es una llamada al proveedor (con IA, una
#: llamada a Anthropic dentro de la petición): se acota a propósito.
LIMITE_DEFECTO = 100
LIMITE_MAXIMO = 200
#: Filas de la lista de clasificaciones (la pantalla pide por días).
LIMITE_LISTA = 500
ETAPA_NUEVO = "Nuevo lead"
ETAPA_SPAM = "Descartado / spam"


@dataclass
class FilaEnSeco:
    contacto_id: str
    nombre: str
    email: str
    fuente: str
    referencia: str
    lead_at: str | None
    web: str | None
    idioma_formulario: str | None
    productos: list[str]
    texto: str
    clasificacion: dict[str, Any]
    ya_clasificado: bool
    haria: dict[str, Any] = field(default_factory=dict)


def _nombre(contact: Contact) -> str:
    partes = [contact.first_name, contact.last_name]
    return " ".join(p for p in partes if p).strip() or (contact.email or "")


def leads_recientes(
    session: Session, *, dias: int, limite: int = LIMITE_DEFECTO,
    ahora: datetime | None = None,
) -> list[tuple[Contact, EntradaLead]]:
    """Los leads de los últimos `dias` (contados desde `ahora`), por su fecha
    real: envíos de formulario que no son spam y notas «form note» de Agile
    (`external_created_at`). Los más recientes primero, hasta `limite`."""
    desde = (ahora or datetime.now(UTC)) - timedelta(days=dias)
    salida: list[tuple[datetime, Contact, EntradaLead]] = []
    envios = session.execute(
        select(FormSubmission, WebForm, Contact)
        .join(WebForm, WebForm.id == FormSubmission.form_id)
        .join(Contact, Contact.id == FormSubmission.contact_id)
        .where(FormSubmission.is_spam.is_(False), FormSubmission.created_at >= desde)
        .order_by(FormSubmission.created_at.desc())
        .limit(limite)
    ).all()
    for envio, form, contact in envios:
        try:
            payload = json.loads(envio.raw_payload_json or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        fecha = registro._fecha(envio.created_at) or desde
        salida.append((fecha, contact, EntradaLead(
            texto=registro.texto_de_envio(form, payload), fuente=FUENTE_FORMULARIO,
            referencia=envio.id, lead_at=fecha, sitio=clave_de_sitio(form.slug),
            formulario=form.slug, idioma_formulario=form.language,
            productos=registro.productos_de_envio(session, form, payload),
            pais=contact.address_country, email=contact.email,
        )))
    # El prefijo «form note» se filtra ya en SQL: con el tope por fuera, un
    # contacto con cientos de notas de llamadas taparía los formularios.
    notas = session.execute(
        select(Note, Contact)
        .join(Contact, Contact.id == Note.contact_id)
        .where(Note.external_system == FUENTE_AGILE, Note.external_created_at >= desde,
               func.lower(Note.body).like("form note%"))
        .order_by(Note.external_created_at.desc())
        .limit(limite)
    ).all()
    for nota, contact in notas:
        if not es_nota_de_formulario(nota.body):
            continue
        fecha = registro._fecha(nota.external_created_at or nota.created_at) or desde
        salida.append((fecha, contact, EntradaLead(
            texto=texto_de_nota(nota.body), fuente=FUENTE_AGILE, referencia=nota.id,
            lead_at=fecha, cuenta_agile=nota.external_account_id,
            pais=contact.address_country, email=contact.email,
        )))
    salida.sort(key=lambda t: t[0], reverse=True)
    return [(c, e) for _, c, e in salida[:limite]]


def simular(
    session: Session, *, dias: int, limite: int = LIMITE_DEFECTO,
    proveedor: Clasificador | None = None,
) -> dict[str, Any]:
    """El informe en seco. No escribe nada."""
    conf = configuracion(session)
    catalogo = intereses.cargar(session)
    proveedor = proveedor or proveedor_por_defecto(catalogo)
    filas: list[FilaEnSeco] = []
    resumen: dict[str, Any] = {
        "total": 0, "spam": 0, "sin_plantilla": 0, "con_discrepancia_idioma": 0,
        "ya_clasificados": 0, "por_interes": {}, "por_idioma": {}, "proveedor": proveedor.nombre,
    }
    for contact, entrada in leads_recientes(session, dias=dias, limite=limite):
        clasificacion = clasificar_lead(entrada, proveedor, catalogo)
        existente = registro.clasificacion_existente(session, entrada)
        haria: dict[str, Any] = {"etapa": ETAPA_SPAM if clasificacion.es_spam else ETAPA_NUEVO,
                                 "plantilla": None, "remitente": None, "tarea": False,
                                 "aviso": None}
        if not clasificacion.es_spam:
            haria["tarea"] = True
            idioma = clasificacion.idioma or contact.language or "es"
            if catalogo.comercial(clasificacion.interes):
                tpl = plantillas.plantilla_para(
                    session, clasificacion.intereses, idioma, conf.get("mapa"), catalogo,
                )
                if tpl is not None:
                    haria["plantilla"] = tpl.name
                else:
                    haria["aviso"] = (f"sin plantilla para "
                                      f"{catalogo.texto(clasificacion.intereses)} en {idioma}")
            else:
                haria["aviso"] = (f"«{catalogo.etiqueta(clasificacion.interes)}» no es un lead "
                                  "comercial: sin plantilla de venta")
            remitente, _marca = remitentes.remitente_para(
                sitio=entrada.sitio, cuenta_agile=entrada.cuenta_agile,
                config=conf.get("remitentes"),
            )
            haria["remitente"] = remitente
            if remitente is None:
                haria["aviso"] = ((haria["aviso"] + "; ") if haria["aviso"] else "") + \
                    "sin remitente: la web del lead no se conoce"
        filas.append(FilaEnSeco(
            contacto_id=contact.id, nombre=_nombre(contact), email=contact.email or "",
            fuente=entrada.fuente, referencia=entrada.referencia,
            lead_at=entrada.lead_at.isoformat() if entrada.lead_at else None,
            web=web_de_sitio(entrada.sitio) if entrada.sitio else None,
            idioma_formulario=entrada.idioma_formulario, productos=list(entrada.productos),
            texto=(entrada.texto or "")[:400],
            clasificacion={
                **clasificacion.como_dict(),
                "interes_texto": catalogo.etiqueta(clasificacion.interes),
                "intereses_texto": catalogo.texto(clasificacion.intereses),
            },
            ya_clasificado=existente is not None, haria=haria,
        ))
        resumen["total"] += 1
        resumen["spam"] += int(clasificacion.es_spam)
        resumen["sin_plantilla"] += int(not clasificacion.es_spam and haria["plantilla"] is None)
        resumen["con_discrepancia_idioma"] += int(clasificacion.discrepancia_idioma)
        resumen["ya_clasificados"] += int(existente is not None)
        resumen["por_interes"][clasificacion.interes] = \
            resumen["por_interes"].get(clasificacion.interes, 0) + 1
        idioma_clave = clasificacion.idioma or "?"
        resumen["por_idioma"][idioma_clave] = resumen["por_idioma"].get(idioma_clave, 0) + 1
    return {
        "dias": dias, "limite": limite, "nada_escrito": True,
        "resumen": resumen, "items": [asdict(f) for f in filas],
    }


def clasificaciones_de(
    session: Session, *, dias: int, limite: int = LIMITE_LISTA,
) -> list[LeadClassification]:
    """Las clasificaciones reales de los últimos N días (para la lista), las
    más recientes primero, hasta `limite`."""
    desde = datetime.now(UTC) - timedelta(days=dias)
    return list(session.scalars(
        select(LeadClassification)
        .where(LeadClassification.created_at >= desde)
        .order_by(LeadClassification.created_at.desc())
        .limit(limite)
    ))
