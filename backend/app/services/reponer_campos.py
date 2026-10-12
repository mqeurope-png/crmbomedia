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
`custom_fields.<clave>`), ni un JSON que no sea un objeto. La marca antigua
`custom_fields` (de antes de marcar por clave) no lo impide: el modal de
entonces no podía quitar claves, así que lo que falta lo quitó un sync.

Las respuestas de los formularios se leen con el mapeo de HOY de cada
formulario (no se guardó el de entonces): si un campo se remapeó a otra
clave, saldrá con la nueva. El informe (`--probar`) lista las claves por
nombre para revisarlas antes de `--apply`.
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
    custom_field_mark,
    custom_value_is_empty,
    decode_custom_fields,
    get_manually_edited_fields,
)

FORMULARIO = "formulario"
WORKFLOW = "workflow"
_PASO_MODIFICAR_CAMPO = "action_set_custom_field"
#: Contactos por consulta al cargarlos, y por commit al reponer.
_LOTE = 200


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
    #: Contactos cuyo JSON no es un objeto: no se tocan.
    no_es_objeto: int = 0
    #: Contactos que están a la vez en Brevo y en Agile (lo suyo vuelve
    #: con una sincronización completa).
    en_brevo_y_agile: int = 0


def _utc(fecha: datetime | None) -> datetime:
    if fecha is None:
        return datetime.min.replace(tzinfo=UTC)
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def _escrituras(session: Session) -> dict[str, list[tuple[datetime, str, dict[str, Any]]]]:
    """Por contacto, lo que escribieron en `custom_fields` los formularios
    y los workflows: `(fecha, origen, {clave: valor})`."""
    from app.services.web_forms.submit import _extract_contact_data  # noqa: PLC0415
    from app.workflows.steps import _CONTACT_NATIVE_FIELDS  # noqa: PLC0415

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
        # Los campos nativos van a su columna, no al JSON: los marca
        # `native`, y los pasos de antes de esa marca se reconocen por el
        # nombre (entonces se colaban en el JSON por error).
        if not isinstance(resultado, dict) or resultado.get("native"):
            continue
        clave = str(resultado.get("field") or "").strip()
        if clave and clave not in _CONTACT_NATIVE_FIELDS:
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
    reponer; None si lo guardado no es un objeto JSON (no se toca)."""
    actuales = decode_custom_fields(contact.custom_fields)
    if actuales is None:
        return None
    marcas = set(get_manually_edited_fields(contact))
    return {
        clave: valor
        for clave, valor in valores.items()
        if clave not in actuales
        and custom_field_mark(clave) not in marcas
        and not custom_value_is_empty(valor)
    }


def planificar(session: Session) -> Plan:
    plan = Plan()
    escrituras = _escrituras(session)
    ids = sorted(escrituras)
    contactos: dict[str, Contact] = {}
    for i in range(0, len(ids), _LOTE):
        lote = ids[i:i + _LOTE]
        contactos.update(
            (c.id, c) for c in session.scalars(select(Contact).where(Contact.id.in_(lote)))
        )
    for contact_id in ids:
        contact = contactos.get(contact_id)
        if contact is None:
            continue
        valores, origen = _rehacer(escrituras[contact_id])
        faltan = _faltan(contact, valores)
        if faltan is None:
            plan.no_es_objeto += 1
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
    return plan


def aplicar(session: Session, plan: Plan, *, actor_email: str, lote: int = _LOTE) -> int:
    """Repone las claves del plan que sigan faltando, con rastro en la
    auditoría, y devuelve cuántos contactos se tocaron.

    Cada contacto se vuelve a leer de la base de datos y bloqueado
    (`SELECT … FOR UPDATE`): entre el informe y esto un sync pudo fusionar
    claves nuevas, y escribir el JSON leído antes las borraría. Hace commit
    cada `lote` contactos, para no tener bloqueados los de los syncs."""
    hechos = 0
    pendientes = 0
    for reposicion in plan.reposiciones:
        contact = session.get(
            Contact, reposicion.contact_id,
            with_for_update=True, populate_existing=True,
        )
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
        pendientes += 1
        if pendientes >= lote:
            session.commit()
            pendientes = 0
    session.commit()
    return hechos
