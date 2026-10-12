"""Quitar de `contacts.custom_fields` el ruido conocido de Brevo.

Sprint Empresas — sub-PR 2 lo hizo con una lista blanca: borraba toda
clave que no estuviera en la de Brevo (`CUSTOM_FIELDS_WHITELIST`). Vuelto
a ejecutar hoy, se llevaría por delante claves buenas que no son de
Brevo: «CONTACTO Persona» de Agile, las de los formularios web, las de
los workflows o las de la ficha. Ahora va al revés: solo quita lo que se
sabe que es ruido, y lo demás lo deja (y lo cuenta en el informe):

- `RUIDO`: atributos internos de Brevo que no son de nadie
  (`sib_contact_owner`, `EXT_ID`).
- Un teléfono secundario (`TELEFONO_2`…) que ya está entre los teléfonos
  del contacto (el sync de Brevo los pasa ahí). Si no está, se queda.

Uso:
    # 1) Informe, SIN escribir (por defecto).
    python -m scripts.cleanup_contact_custom_fields_whitelist --probar

    # 2) Quitar el ruido de la lista.
    python -m scripts.cleanup_contact_custom_fields_whitelist --apply

Idempotente: una fila sin ruido no se toca. Una fila que se queda sin
claves pasa a NULL. Escribe en lotes de 200.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.integrations.brevo.mapper import SECONDARY_PHONE_ATTRS, _normalise_attr_key
from app.models.crm import Contact, ContactPhone
from app.services.contact_sync_protection import decode_custom_fields

log = logging.getLogger("cleanup_custom_fields")
logging.basicConfig(level=logging.INFO, format="%(message)s")

#: Atributos internos de Brevo, en la forma de `_normalise_attr_key`.
RUIDO: frozenset[str] = frozenset({"SIB_CONTACT_OWNER", "EXT_ID"})

_TELEFONOS: frozenset[str] = frozenset(
    _normalise_attr_key(k) for k in SECONDARY_PHONE_ATTRS
)


def _digitos(valor: Any) -> str:
    return "".join(c for c in str(valor) if c.isdigit() or c == "+")


def claves_de_ruido(campos: dict[str, Any], telefonos: set[str]) -> list[str]:
    """Las claves de `campos` que se pueden quitar sin perder nada:
    `RUIDO` y los teléfonos secundarios que ya están en `telefonos` (sus
    dígitos)."""
    quitar: list[str] = []
    for clave, valor in campos.items():
        normal = _normalise_attr_key(clave)
        if normal in RUIDO:
            quitar.append(clave)
        elif normal in _TELEFONOS and valor is not None:
            digitos = _digitos(valor)
            if digitos and digitos in telefonos:
                quitar.append(clave)
    return quitar


def cleanup(
    *, dry_run: bool, batch: int = 200, limit: int | None = None
) -> dict[str, Any]:
    counts: dict[str, Any] = {
        "scanned": 0,
        "contacts_touched": 0,
        "keys_removed": 0,
        "rows_cleared_to_null": 0,
        "not_json_object": 0,
        "removed_key_freq": Counter(),
        "kept_key_freq": Counter(),
    }
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(Contact).where(Contact.custom_fields.is_not(None))
        if limit is not None:
            stmt = stmt.limit(limit)
        contacts = list(session.scalars(stmt))
        counts["scanned"] = len(contacts)
        telefonos: dict[str, set[str]] = defaultdict(set)
        for contact_id, number in session.execute(
            select(ContactPhone.contact_id, ContactPhone.number)
        ):
            digitos = _digitos(number or "")
            if digitos:
                telefonos[contact_id].add(digitos)
        pending = 0
        for contact in contacts:
            campos = decode_custom_fields(contact.custom_fields)
            if campos is None:
                # No es un objeto JSON: no se toca.
                counts["not_json_object"] += 1
                continue
            quitar = claves_de_ruido(campos, telefonos.get(contact.id, set()))
            for clave in campos:
                if clave not in quitar:
                    counts["kept_key_freq"][clave] += 1
            if not quitar:
                continue
            counts["contacts_touched"] += 1
            counts["keys_removed"] += len(quitar)
            for clave in quitar:
                counts["removed_key_freq"][clave] += 1
            quedan = {k: v for k, v in campos.items() if k not in quitar}
            if not quedan:
                counts["rows_cleared_to_null"] += 1
            if not dry_run:
                contact.custom_fields = (
                    json.dumps(quedan, default=str) if quedan else None
                )
                pending += 1
                if pending >= batch:
                    session.commit()
                    pending = 0
        if not dry_run and pending:
            session.commit()
        if dry_run:
            session.rollback()
    # Las 20 más frecuentes de cada lista bastan para ver qué hay.
    counts["removed_key_freq"] = dict(counts["removed_key_freq"].most_common(20))
    counts["kept_key_freq"] = dict(counts["kept_key_freq"].most_common(20))
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--probar", action="store_true",
                      help="solo informa (por defecto): no escribe nada")
    modo.add_argument("--dry-run", action="store_true",
                      help="lo mismo que --probar")
    modo.add_argument("--apply", action="store_true",
                      help="quita el ruido de la lista")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    summary = cleanup(dry_run=not args.apply, limit=args.limit)
    log.info(
        "cleanup summary scanned=%d touched=%d keys_removed=%d "
        "rows_cleared_to_null=%d not_json_object=%d escrito=%s",
        summary["scanned"],
        summary["contacts_touched"],
        summary["keys_removed"],
        summary["rows_cleared_to_null"],
        summary["not_json_object"],
        bool(args.apply),
    )
    print(json.dumps(summary, default=str, ensure_ascii=False))
    if not args.apply:
        print("Nada escrito. Para quitar el ruido de la lista: --apply.")


if __name__ == "__main__":
    main()
