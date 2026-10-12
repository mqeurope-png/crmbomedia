"""Reponer los campos personalizados que la sincronización borraba.

Hasta el arreglo de octubre de 2026, cada sincronización (Brevo, Agile)
sustituía el JSON `contacts.custom_fields` entero por lo que traía ella y
se llevaba lo demás: las claves de la otra integración o de otra cuenta, y
las que escribe BoHub mismo —un formulario web (`contact.custom.<clave>`)
o el paso «Modificar campo» de un workflow—. Ahora se fusiona clave a
clave (`contact_sync_protection.merge_custom_fields`).

Lo de Brevo y lo de Agile vuelve con una sincronización completa: sus
syncs ya no borran nada. Lo que escribió BoHub solo se puede reponer desde
su rastro: las respuestas de los formularios (`form_submissions`) y el
historial de los workflows (`workflow_run_history`). Aquí se rehace, por
contacto y por orden de fecha, lo que dejaron —el formulario solo rellena
claves vacías; el workflow escribe— y se reponen las claves que FALTAN hoy.
Nunca se pisa una clave que exista, ni una quitada a mano (su marca
`custom_fields.<clave>`), ni un contacto con el JSON entero protegido (la
marca antigua `custom_fields`), ni un JSON que no sea un objeto.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.models.crm import Contact, ExternalReference, ExternalSystem
from app.models.web_forms import FormSubmission, WebForm
from app.models.workflows import WorkflowRunHistory
from app.services.contact_sync_protection import (
    CUSTOM_FIELDS,
    custom_field_mark,
    decode_custom_fields,
    get_manually_edited_fields,
)

FORMULARIO = "formulario"
WORKFLOW = "workflow"
_PASO_MODIFICAR_CAMPO = "action_set_custom_field"


@dataclass
class Reposicion:
    contact_id: str
    #: clave → valor que se repone.
    claves: dict[str, Any]
    #: clave → de dónde sale (`FORMULARIO` o `WORKFLOW`).
    origen: dict[str, str]


@dataclass
class Plan:
    reposiciones: list[Reposicion] = field(default_factory=list)
    #: Contactos con el JSON entero protegido a mano: no se tocan.
    protegidos: int = 0
    #: Contactos cuyo JSON no es un objeto: no se tocan.
    no_es_objeto: int = 0
    #: Contactos que están a la vez en Brevo y en Agile (lo suyo vuelve
    #: con una sincronización completa).
    en_brevo_y_agile: int = 0


def _utc(fecha: datetime | None) -> datetime:
    if fecha is None:
        return datetime.min.replace(tzinfo=UTC)
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def _vacio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _escrituras(session: Session) -> dict[str, list[tuple[datetime, str, dict[str, Any]]]]:
    """Por contacto, lo que escribieron en `custom_fields` los formularios
    y los workflows: `(fecha, origen, {clave: valor})`."""
    from app.services.web_forms.submit import _extract_contact_data  # noqa: PLC0415

    por_contacto: dict[str, list[tuple[datetime, str, dict[str, Any]]]] = defaultdict(list)
    formularios: dict[str, WebForm | None] = {}
    respuestas = session.scalars(
        select(FormSubmission).where(
            FormSubmission.contact_id.is_not(None),
            FormSubmission.is_spam.is_(False),
        )
    )
    for respuesta in respuestas:
        if respuesta.form_id not in formularios:
            formularios[respuesta.form_id] = session.get(WebForm, respuesta.form_id)
        formulario = formularios[respuesta.form_id]
        if formulario is None:
            continue
        try:
            payload = json.loads(respuesta.raw_payload_json or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        _, custom, _ = _extract_contact_data(formulario, payload)
        if custom:
            por_contacto[respuesta.contact_id].append(
                (_utc(respuesta.created_at), FORMULARIO, custom)
            )
    pasos = session.scalars(
        select(WorkflowRunHistory).where(
            WorkflowRunHistory.step_type == _PASO_MODIFICAR_CAMPO,
            WorkflowRunHistory.status == "ok",
        )
    )
    for paso in pasos:
        try:
            resultado = json.loads(paso.result_json or "{}")
        except (TypeError, ValueError):
            continue
        # Los campos nativos (`native`) van a su columna, no al JSON.
        if not isinstance(resultado, dict) or resultado.get("native"):
            continue
        clave = str(resultado.get("field") or "").strip()
        if clave:
            por_contacto[paso.contact_id].append(
                (_utc(paso.executed_at), WORKFLOW, {clave: resultado.get("value")})
            )
    return por_contacto


def _rehacer(
    escrituras: list[tuple[datetime, str, dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Lo que dejaron, en orden de fecha: el formulario solo rellena las
    claves vacías (`setdefault`); el workflow escribe."""
    valores: dict[str, Any] = {}
    origen: dict[str, str] = {}
    for _, de_donde, claves in sorted(escrituras, key=lambda e: e[0]):
        for clave, valor in claves.items():
            if de_donde == FORMULARIO and clave in valores:
                continue
            valores[clave] = valor
            origen[clave] = de_donde
    return valores, origen


def _faltan(contact: Contact, valores: dict[str, Any]) -> dict[str, Any] | None:
    """Las claves de `valores` que faltan en el contacto y se pueden
    reponer; None si el contacto no se puede tocar."""
    actuales = decode_custom_fields(contact.custom_fields)
    if actuales is None:
        return None
    marcas = set(get_manually_edited_fields(contact))
    if CUSTOM_FIELDS in marcas:
        return None
    return {
        clave: valor
        for clave, valor in valores.items()
        if clave not in actuales
        and custom_field_mark(clave) not in marcas
        and not _vacio(valor)
    }


def planificar(session: Session) -> Plan:
    plan = Plan()
    for contact_id, escrituras in _escrituras(session).items():
        contact = session.get(Contact, contact_id)
        if contact is None:
            continue
        valores, origen = _rehacer(escrituras)
        faltan = _faltan(contact, valores)
        if faltan is None:
            if decode_custom_fields(contact.custom_fields) is None:
                plan.no_es_objeto += 1
            else:
                plan.protegidos += 1
            continue
        if faltan:
            plan.reposiciones.append(
                Reposicion(
                    contact_id=contact_id,
                    claves=faltan,
                    origen={clave: origen[clave] for clave in faltan},
                )
            )
    en_brevo = select(ExternalReference.contact_id).where(
        ExternalReference.system == ExternalSystem.BREVO
    )
    en_agile = select(ExternalReference.contact_id).where(
        ExternalReference.system == ExternalSystem.AGILECRM
    )
    plan.en_brevo_y_agile = session.scalar(
        select(func.count(Contact.id)).where(
            Contact.id.in_(en_brevo), Contact.id.in_(en_agile)
        )
    ) or 0
    plan.reposiciones.sort(key=lambda r: r.contact_id)
    return plan


def aplicar(session: Session, plan: Plan, *, actor_email: str) -> int:
    """Repone las claves del plan que sigan faltando (se vuelve a mirar
    cada contacto: entre el informe y esto pudo cambiar), con rastro en la
    auditoría. Devuelve cuántos contactos se tocaron. No hace commit."""
    hechos = 0
    for reposicion in plan.reposiciones:
        contact = session.get(Contact, reposicion.contact_id)
        if contact is None:
            continue
        faltan = _faltan(contact, reposicion.claves)
        if not faltan:
            continue
        actuales = decode_custom_fields(contact.custom_fields) or {}
        contact.custom_fields = json.dumps({**actuales, **faltan}, default=str)
        record_event(
            session,
            action=Action.CONTACT_CUSTOM_FIELDS_RESTORED,
            target_type="contact",
            target_id=contact.id,
            actor_email=actor_email,
            metadata={
                "claves": sorted(faltan),
                "origen": {clave: reposicion.origen[clave] for clave in sorted(faltan)},
            },
        )
        hechos += 1
    session.flush()
    return hechos
