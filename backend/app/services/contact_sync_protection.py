"""PR-Fix-Sync-No-Sobreescribe-Cambios-CRM.

Política de protección de campos del Contact frente a syncs externos
(AgileCRM, Brevo). Bart confirmó (2026-06-21) que los syncs estaban
machacando todo: lead_score, owner, teléfono, lo que el comercial
hubiera tocado en el CRM volvía a la versión externa en máximo 1h.

Separamos los campos en dos capas + tags merge:

- **Capa A**: campos del contacto compartidos con sistemas externos.
  Por defecto el sync los actualiza. Si el operador los editó
  manualmente (anotado en `Contact.manually_edited_fields_json`), el
  sync los respeta y NO los sobrescribe.

- **Capa B**: campos exclusivos del CRM (owner asignado por reglas,
  scoring, notas internas, custom fields creados desde
  `/admin/custom-fields` con `source='manual'`). El sync NUNCA los
  toca, independientemente del array de protección.

- **Tags**: merge — el sync solo *añade*. Las tags se quitan
  manualmente desde la UI y no vuelven a aparecer aunque el sistema
  externo las tenga. El operador es la fuente de verdad.

- **Eventos Brevo** (opens/clicks/bounces/unsubscribes): siempre se
  importan a `email_message_events` sin tocar el contacto. No entran
  en esta política.

El helper `mark_manually_edited` se llama desde el PATCH del contacto;
`is_field_protected` lo consulta el sync antes de cada SET en Capa A.
`reset_manual_edits` lo consume el endpoint `POST
/api/contacts/{id}/reset-manual-edits` cuando el operador quiere
"volver a aceptar el valor de Agile/Brevo".
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.crm import Contact


#: Campos del Contact que viven en sistemas externos y que el sync
#: actualiza salvo que el operador los haya editado manualmente.
#: Mantener sincronizado con el mapper Agile/Brevo: lo que esté aquí
#: tiene que cubrirse en `_apply_update_with_protection`.
LAYER_A_FIELDS: frozenset[str] = frozenset({
    "first_name",
    "last_name",
    "email",
    "phone",
    "company_id",
    "job_title",
    "linkedin_url",
    "personal_website",
    "address_line",
    "address_city",
    "address_state",
    "address_postal_code",
    "address_country",
    "address_country_name",
    "address_region",
    # `custom_fields` es JSON serializado — el helper compara dict-vs-dict
    # antes de marcar, así que no se marca por re-serializaciones
    # del mismo contenido.
    "custom_fields",
})

#: Campos del Contact que SOLO viven en el CRM. El sync NUNCA los
#: actualiza. No hace falta marcarlos manualmente — la protección es
#: incondicional.
LAYER_B_FIELDS: frozenset[str] = frozenset({
    "owner_user_id",
    "lead_score",
    "star_rating",
    "commercial_status",
    "marketing_consent",
    "is_active",
    "is_email_valid",
    # `origin` y `origin_account_id` los pone el sync mismo en el
    # primer insert; no son editables desde la UI manual. Los
    # incluimos por defensa contra accidentes.
    "origin",
    "origin_account_id",
})

#: Set completo de campos que el banner del modal Editar puede
#: rastrear. Incluye Capa A (donde la marca DESHABILITA el sync) y
#: Capa B (donde la marca es solo trazabilidad — el sync nunca toca
#: Capa B por diseño). El banner muestra los dos tipos.
MARKABLE_FIELDS: frozenset[str] = LAYER_A_FIELDS | LAYER_B_FIELDS


def _load(contact: Contact) -> list[str]:
    raw = contact.manually_edited_fields_json
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(f) for f in data] if isinstance(data, list) else []


def _store(contact: Contact, fields: list[str]) -> None:
    if not fields:
        contact.manually_edited_fields_json = None
        return
    deduped = sorted(dict.fromkeys(fields))
    contact.manually_edited_fields_json = json.dumps(deduped)


def get_manually_edited_fields(contact: Contact) -> list[str]:
    """Lee el array como `list[str]` (siempre, aunque la columna sea
    NULL o JSON corrupto)."""
    return _load(contact)


def mark_manually_edited(contact: Contact, field_names: list[str]) -> None:
    """Añade los `field_names` al array de protección del contacto.
    Idempotente — deduplica. Filtra a `MARKABLE_FIELDS` (Capa A +
    Capa B); ignora typos / campos desconocidos.

    PR-Fix-Patch-No-Marca-Manual-Edits: ahora también marca campos
    de Capa B (lead_score, star_rating, owner, etc.). Bart pidió
    trazabilidad — el banner del modal debe mostrar TODO campo
    editado manualmente aunque su protección sea incondicional. El
    sync sigue tratando Capa B como siempre protegida sin importar
    el array, así que marcarlos no cambia el comportamiento, solo
    el badge."""
    if not field_names:
        return
    relevant = [f for f in field_names if f in MARKABLE_FIELDS]
    if not relevant:
        return
    existing = _load(contact)
    merged = existing + [f for f in relevant if f not in existing]
    _store(contact, merged)


def reset_manual_edits(
    contact: Contact, field_names: list[str] | None = None
) -> None:
    """Quita marcas del array. `None` o lista vacía → vacía todo el
    array (el próximo sync sobrescribirá cualquier campo de Capa A).
    Lista concreta → quita solo esos."""
    if not field_names:
        contact.manually_edited_fields_json = None
        return
    existing = _load(contact)
    remaining = [f for f in existing if f not in set(field_names)]
    _store(contact, remaining)


def is_field_protected(contact: Contact, field_name: str) -> bool:
    """True si el sync NO debe sobrescribir `field_name`:
    - Campo de Capa B → siempre protegido.
    - Campo de Capa A → protegido sólo si está en el array.
    - Otros → no protegidos (sync libre)."""
    if field_name in LAYER_B_FIELDS:
        return True
    if field_name in LAYER_A_FIELDS:
        return field_name in set(_load(contact))
    return False


def apply_sync_update(
    contact: Contact,
    record: dict,
    *,
    allow_email_overwrite: bool = True,
) -> dict[str, object]:
    """Aplica `record` (lo que produjo el mapper externo) a `contact`
    respetando la política de protección. Devuelve un dict con los
    campos que SÍ se cambiaron (para audit / logging).

    Centraliza la lógica para que el sync de Agile y el de Brevo no
    dupliquen el patrón. Antes existía `_apply_update` por integración;
    este wrapper reemplaza esas funciones."""
    changes: dict[str, object] = {}
    for field, value in record.items():
        if field in LAYER_B_FIELDS:
            # Capa B nunca se toca desde sync.
            continue
        if field == "email" and not allow_email_overwrite:
            # Política específica de Brevo cuando consolida por email:
            # no pisa el email del contacto existente.
            continue
        if is_field_protected(contact, field):
            continue
        current = getattr(contact, field, None)
        if current != value:
            setattr(contact, field, value)
            changes[field] = value
    return changes
