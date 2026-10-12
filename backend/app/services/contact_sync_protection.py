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

- **`custom_fields`** (el JSON de campos personalizados): se fusiona
  **clave a clave**, nunca se sustituye entero. Cada sync escribe solo
  las claves que trae con valor; las demás —las de la otra integración
  o de otra cuenta, las de un formulario web, un workflow o la ficha—
  se quedan. Solo quita una clave que ESE origen trajo la última vez y
  ya no trae, si sigue con el valor que él puso (lo borraron allí y
  nadie lo ha tocado aquí: `drop_released_custom_fields`). Cada clave
  editada a mano se marca por separado (`custom_fields.<clave>`) y el
  sync no la toca; la marca `custom_fields` a secas (la de antes de
  fusionar por clave) protege todas las claves que ya tiene el contacto,
  y deja añadir las que falten.

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
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.crm import Contact

logger = logging.getLogger(__name__)


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
    # `custom_fields` es JSON serializado y NO se sustituye entero: el
    # sync lo fusiona clave a clave (`merge_custom_fields`) y el PATCH
    # marca cada clave editada por separado (`custom_fields.<clave>`).
    "custom_fields",
})

#: El JSON de campos personalizados y el prefijo de la marca de UNA de sus
#: claves editada a mano (`custom_fields.Horario`). La marca `custom_fields`
#: a secas es la de antes de fusionar por clave: protege el JSON entero.
CUSTOM_FIELDS = "custom_fields"
CUSTOM_FIELD_MARK_PREFIX = f"{CUSTOM_FIELDS}."

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


def custom_field_mark(key: str) -> str:
    """La marca de una clave de `custom_fields` editada a mano."""
    return f"{CUSTOM_FIELD_MARK_PREFIX}{key}"


def _is_markable(name: str) -> bool:
    return name in MARKABLE_FIELDS or (
        name.startswith(CUSTOM_FIELD_MARK_PREFIX)
        and len(name) > len(CUSTOM_FIELD_MARK_PREFIX)
    )


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
    el badge.

    Las claves de `custom_fields` se marcan una a una
    (`custom_field_mark(clave)`)."""
    if not field_names:
        return
    relevant = [f for f in field_names if _is_markable(f)]
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
    Lista concreta → quita solo esos; `custom_fields` quita también las
    marcas de cada una de sus claves."""
    if not field_names:
        contact.manually_edited_fields_json = None
        return
    quitar = set(field_names)
    todo_el_json = CUSTOM_FIELDS in quitar
    existing = _load(contact)
    remaining = [
        f for f in existing
        if f not in quitar
        and not (todo_el_json and f.startswith(CUSTOM_FIELD_MARK_PREFIX))
    ]
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


def decode_custom_fields(raw: Any) -> dict[str, Any] | None:
    """El JSON de `custom_fields` como dict: `{}` si está vacío (NULL,
    cadena vacía, `null`) y None si guarda algo que no es un objeto JSON
    (no se puede fusionar con nada sin destruirlo)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    texto = str(raw).strip()
    if not texto:
        return {}
    try:
        valor = json.loads(texto)
    except (TypeError, ValueError):
        return None
    if valor is None:
        return {}
    return valor if isinstance(valor, dict) else None


def _como_texto(valor: Any) -> str:
    """Un valor tal como lo enseña —y lo devuelve— el modal de la ficha
    (`String(v)` en el navegador, `""` para null)."""
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


def _mismo_valor(guardado: Any, nuevo: Any) -> bool:
    if guardado == nuevo:
        return True
    if isinstance(guardado, (dict, list)) or isinstance(nuevo, (dict, list)):
        return False
    return _como_texto(guardado) == _como_texto(nuevo)


def custom_value_is_empty(valor: Any) -> bool:
    """Un valor que no cuenta como dato: ni lo escribe un sync ni se
    repone (`None`, texto vacío o solo espacios)."""
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _clave_protegida(clave: str, marcas: set[str], actuales: dict[str, Any]) -> bool:
    """La clave editada a mano (`custom_fields.<clave>`), o cualquier clave
    que ya tenga el contacto si lleva la marca antigua `custom_fields`."""
    return custom_field_mark(clave) in marcas or (
        CUSTOM_FIELDS in marcas and clave in actuales
    )


def merge_custom_fields(contact: Contact, incoming: Any) -> str | None:
    """Las claves que trae un sync, fusionadas en el JSON del contacto:
    el JSON resultante, o None si no cambia nada.

    Solo se escriben las claves que trae, con valor; las demás se quedan
    (las de la otra integración o de otra cuenta, las de un formulario,
    un workflow o la ficha). Las claves protegidas a mano no se tocan
    (`_clave_protegida`). Si lo guardado no es un objeto JSON no se pisa:
    se deja y se avisa en el log."""
    nuevos = decode_custom_fields(incoming)
    if not nuevos:
        return None
    actuales = decode_custom_fields(contact.custom_fields)
    if actuales is None:
        logger.warning(
            "contact_sync_protection: custom_fields del contacto %s no es un "
            "objeto JSON; el sync no lo fusiona",
            getattr(contact, "id", "?"),
        )
        return None
    marcas = set(_load(contact))
    resultado = dict(actuales)
    for clave, valor in nuevos.items():
        if custom_value_is_empty(valor) or _clave_protegida(clave, marcas, actuales):
            continue
        resultado[clave] = valor
    if resultado == actuales:
        return None
    return json.dumps(resultado, default=str)


def custom_fields_from_reference(ref: Any) -> dict[str, Any] | None:
    """Lo que un origen (una `ExternalReference`) trajo en `custom_fields`
    la última vez que se sincronizó, o None si no consta (referencias de
    antes de guardarlo: entonces no se quita nada)."""
    raw = getattr(ref, "metadata_json", None)
    if not raw:
        return None
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(metadata, dict):
        return None
    campos = metadata.get(CUSTOM_FIELDS)
    return campos if isinstance(campos, dict) else None


def drop_released_custom_fields(
    contact: Contact, previous: dict[str, Any] | None, current: dict[str, Any],
) -> list[str]:
    """Quita las claves que ese origen trajo la última vez (`previous`) y ya
    no trae (`current`), si siguen con el valor que él puso: las borraron
    allí y nadie las ha tocado aquí. Una clave que cambió otro (otra cuenta,
    un formulario, un workflow, la ficha) o que está protegida a mano se
    queda. Sin `previous` no se quita nada. Devuelve las claves quitadas."""
    if not previous:
        return []
    actuales = decode_custom_fields(contact.custom_fields)
    if not actuales:
        return []
    marcas = set(_load(contact))
    quitar = [
        clave for clave, valor in previous.items()
        if clave not in current
        and clave in actuales
        and actuales[clave] == valor
        and not _clave_protegida(clave, marcas, actuales)
    ]
    if quitar:
        for clave in quitar:
            del actuales[clave]
        contact.custom_fields = json.dumps(actuales, default=str) if actuales else None
    return quitar


def release_dropped_custom_fields(
    contact: Contact, ref: Any, extras: dict[str, Any] | None,
) -> list[str]:
    """`drop_released_custom_fields` para el upsert de un sync: lo de la
    última vez sale de los metadatos de la referencia (`ref`) y lo de
    ahora, de los que trae el mapper (`extras["metadata"]["custom_fields"]`).
    Hay que llamarlo ANTES de guardar los extras nuevos en la referencia."""
    metadata = (extras or {}).get("metadata") or {}
    actual = metadata.get(CUSTOM_FIELDS) if isinstance(metadata, dict) else None
    if not isinstance(actual, dict):
        # El mapper no dice qué trae: no se quita nada.
        return []
    return drop_released_custom_fields(
        contact, custom_fields_from_reference(ref), actual
    )


def apply_manual_custom_fields(contact: Contact, payload: Any) -> list[str]:
    """Aplica lo que manda la ficha (PATCH) en `custom_fields` y devuelve
    las marcas de las claves que cambian de verdad, para
    `mark_manually_edited`.

    Clave a clave, como el sync: solo se tocan las claves que vienen; una
    clave con valor `None` se quita; las que no vienen se quedan (una
    sincronización pudo añadir alguna con el modal abierto). `None` (o
    cadena vacía) en lugar del objeto vacía el JSON entero y marca cada
    clave quitada, para que el sync no las vuelva a traer. Un valor igual
    al guardado no cuenta como cambio: el modal lo manda como texto (`5`
    y `"5"` son lo mismo). ValueError si llega algo que no es un objeto."""
    if isinstance(payload, str):
        if payload.strip():
            payload = decode_custom_fields(payload)
            if payload is None:
                raise ValueError("custom_fields tiene que ser un objeto JSON.")
        else:
            payload = None
    actuales = decode_custom_fields(contact.custom_fields)
    if payload is None:
        contact.custom_fields = None
        return [custom_field_mark(str(k)) for k in actuales or {}]
    if not isinstance(payload, dict):
        raise ValueError("custom_fields tiene que ser un objeto JSON.")
    # Lo guardado no era un objeto JSON (legado): la ficha lo sustituye.
    sustituye = actuales is None
    resultado = dict(actuales or {})
    marcas: list[str] = []
    for clave_cruda, valor in payload.items():
        clave = str(clave_cruda)
        if valor is None:
            if clave in resultado:
                del resultado[clave]
                marcas.append(custom_field_mark(clave))
            continue
        if clave in resultado and _mismo_valor(resultado[clave], valor):
            continue
        resultado[clave] = valor
        marcas.append(custom_field_mark(clave))
    if marcas or sustituye:
        contact.custom_fields = (
            json.dumps(resultado, default=str) if resultado else None
        )
    return marcas


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
    este wrapper reemplaza esas funciones.

    `custom_fields` no se sustituye: se fusiona clave a clave
    (`merge_custom_fields`, que mira las marcas de cada clave)."""
    changes: dict[str, object] = {}
    for field, value in record.items():
        if field in LAYER_B_FIELDS:
            # Capa B nunca se toca desde sync.
            continue
        if field == "email" and not allow_email_overwrite:
            # Política específica de Brevo cuando consolida por email:
            # no pisa el email del contacto existente.
            continue
        if field == CUSTOM_FIELDS:
            merged = merge_custom_fields(contact, value)
            if merged is not None:
                contact.custom_fields = merged
                changes[field] = merged
            continue
        if is_field_protected(contact, field):
            continue
        current = getattr(contact, field, None)
        if current != value:
            setattr(contact, field, value)
            changes[field] = value
    return changes
